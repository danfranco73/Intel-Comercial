"""Bounded V1 monetary repair. Explicit clients only; never discovers credentials.

Apply and rollback use the same deterministic, synchronous batches. The journal
and destination-specific backups must live on durable storage before any write.
The caller must exclude concurrent ingestion throughout apply/validation/rollback.
"""
from copy import deepcopy
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re

from bson import json_util
from pymongo import UpdateOne

from sales_coach.domain.sales_v2 import multiset_hash
from scripts.analyze_sales_phase1 import KEY, PATCH_FIELDS, key
from scripts.audit_sales_phase1 import require, number, row_fingerprint

START, END = "2026-09-22", "2026-09-28"
EXPECTED_ROWS = 17805
PROPOSAL_SHA = "9a0ffe3a5468d02a7f9f1c185d6c3b4625918df12e858ce516bfa240371f2ea0"


def in_scope(row):
    return START <= str(row["date"])[:10] <= END


def quoted(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r").replace("\0", "\\0") + "'"


def encoded_key(row):
    values = key(row)
    require(all("\x1f" not in v for v in values), "Ambiguous key separator")
    return "\x1f".join(values)


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class MongoStore:
    name = "mongo"

    def __init__(self, collection):
        self.collection = collection

    def read(self):
        return list(self.collection.find({"date": {"$gte": START, "$lte": END}}))

    def write(self, pairs):
        operations = []
        for current, target, fields in pairs:
            require(in_scope(current), "Mongo write outside period")
            predicate = {"_id": current["_id"],
                         **{f: current[f] if f in current else {"$exists": False} for f in KEY},
                         **{f: current[f] for f in PATCH_FIELDS}}
            operations.append(UpdateOne(predicate, {"$set": {f: target[f] for f in fields}}, upsert=False))
        if operations:
            result = self.collection.bulk_write(operations, ordered=True)
            require(result.matched_count == len(operations), "Mongo compare-and-set mismatch; rollback required")


class ClickHouseStore:
    name = "clickhouse"

    def __init__(self, client, table):
        require(bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", table)), "Unsafe table name")
        self.client, self.table = client, table

    def read(self):
        database, table = self.table.split(".")
        pending = self.client.query("SELECT count() FROM system.mutations WHERE database=" + quoted(database) +
                                    " AND table=" + quoted(table) + " AND is_done=0").result_rows[0][0]
        require(pending == 0, "Unfinished ClickHouse mutation; wait for completion before validation or rollback")
        result = self.client.query(f"SELECT * FROM {self.table} WHERE date >= '{START}' AND date <= '{END}'")
        return [dict(zip(result.column_names, row)) for row in result.result_rows]

    def write(self, pairs):
        # No staging table, joins, nondeterministic subqueries, DELETE or INSERT.
        # String keys are injective after separator validation during preflight.
        expression = "concat(" + ",char(31),".join(f"toString({f})" for f in KEY) + ")"
        assignments, predicates = [], []
        for current, target, fields in pairs:
            require(in_scope(current), "ClickHouse write outside period")
            predicates.append("tuple(" + quoted(encoded_key(current)) + "," +
                              ",".join(repr(float(current[f])) for f in PATCH_FIELDS) + ")")
        for field in PATCH_FIELDS:
            subset = [(current, target) for current, target, fields in pairs if field in fields]
            if subset:
                keys = ",".join(quoted(encoded_key(a)) for a, _ in subset)
                values = ",".join(repr(float(b[field])) for _, b in subset)
                assignments.append(f"{field}=transform({expression},[{keys}],[{values}],{field})")
        if assignments:
            sql = (f"ALTER TABLE {self.table} UPDATE " + ",".join(assignments) +
                   f" WHERE date >= '{START}' AND date <= '{END}' AND tuple(" + expression + "," +
                   ",".join(PATCH_FIELDS) + ") IN (" + ",".join(predicates) + ")")
            self.client.command(sql, settings={"mutations_sync": 2, "max_query_size": 1048576})


def expected_rows(original, changes):
    rows = deepcopy(original)
    index = {key(r): r for r in rows}
    require(len(index) == len(rows), "Non-unique destination keys")
    for row in rows:
        encoded_key(row)
    require(len(changes) == EXPECTED_ROWS and len({key(c['key']) for c in changes}) == EXPECTED_ROWS,
            "Scope must contain exactly 17805 distinct records")
    for change in changes:
        require(set(change["key"]) == set(KEY) and in_scope(change["key"]), "Invalid repair key/period")
        require(bool(change["after"]) and set(change["after"]) <= set(PATCH_FIELDS), "Unauthorized repair fields")
        row = index[key(change["key"])]
        require(row_fingerprint([row]) == change["before_economic_fingerprint"], "Economic precondition mismatch")
        require(all(number(row[f]) == number(change["before"][f]) for f in PATCH_FIELDS), "Money precondition mismatch")
        for field, value in change["after"].items():
            row[field] = float(number(value))
    return rows


class RepairExecutor:
    """One executor for both destinations and both directions, with restartable rollback.

    Constructor acquires a local lock. A production orchestrator additionally
    needs its global ingestion lock/maintenance window; no CLI enables production.
    """
    def __init__(self, stores, originals, changes, directory, *, resume=False):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=resume)
        self.lock = (self.directory / "executor.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.stores, self.originals, self.changes = stores, originals, changes
        self.expected = {s.name: expected_rows(originals[s.name], changes) for s in stores}
        self.journal_path = self.directory / "journal.json"
        if resume:
            self.journal = json.loads(self.journal_path.read_text())
            require(self.journal["changes_hash"] == multiset_hash(changes), "Journal proposal mismatch")
            for s in stores:
                require(self.journal["initial"][s.name] == multiset_hash(originals[s.name]), "Journal backup mismatch")
                backup = self.directory / (s.name + "-backup.jsonl.gz")
                require(hashlib.sha256(backup.read_bytes()).hexdigest() == self.journal["backup_sha256"][s.name], "Backup file damaged")
        else:
            for s in stores:
                require(multiset_hash(s.read()) == multiset_hash(originals[s.name]), "Destination full precondition mismatch")
            backup_hashes = {}
            for s in stores:
                backup = self.directory / (s.name + "-backup.jsonl.gz")
                with gzip.open(backup, "xt") as f:
                    for row in originals[s.name]:
                        f.write(json_util.dumps(row, default=str) + "\n")
                with backup.open("rb") as f:
                    os.fsync(f.fileno())
                backup_hashes[s.name] = hashlib.sha256(backup.read_bytes()).hexdigest()
            self.journal = {"status": "prepared", "changes_hash": multiset_hash(changes),
                            "initial": {s.name: multiset_hash(originals[s.name]) for s in stores},
                            "expected": {s.name: multiset_hash(self.expected[s.name]) for s in stores},
                            "backup_sha256": backup_hashes, "batches": []}
            atomic_json(self.journal_path, self.journal)

    def execute(self, direction, *, batch_size=500, fail_after_batches=None):
        require(direction in ("apply", "rollback"), "Invalid direction")
        require(1 <= batch_size <= 500, "Unsafe batch size")
        require(direction == "rollback" or self.journal["status"] == "prepared", "Apply cannot be resumed blindly; rollback first")
        targets = self.expected if direction == "apply" else self.originals
        # Validate BOTH destinations before touching either, including on restart.
        pending = {}
        for store in self.stores:
            name = store.name
            current = store.read()
            actual = {key(r): r for r in current}
            before = {key(r): r for r in self.originals[name]}
            after = {key(r): r for r in self.expected[name]}
            target = {key(r): r for r in targets[name]}
            require(len(actual) == len(current) and actual.keys() == before.keys(), "Destination row identity changed")
            for k, row in actual.items():
                require(multiset_hash([row]) in (multiset_hash([before[k]]), multiset_hash([after[k]])),
                        "Unexpected state; refusing to overwrite external changes")
            if direction == "apply":
                require(multiset_hash(current) == self.journal["initial"][name], "Apply baseline drift")
            pending[name] = [(actual[key(c["key"])], target[key(c["key"])], tuple(c["after"]))
                             for c in self.changes if multiset_hash([actual[key(c["key"])]]) != multiset_hash([target[key(c["key"])]])]
        self.journal["status"] = direction + "_in_progress"
        atomic_json(self.journal_path, self.journal)
        completed = 0
        for store in self.stores:
            pairs = pending[store.name]
            for offset in range(0, len(pairs), batch_size):
                batch = pairs[offset:offset + batch_size]
                event = {"direction": direction, "store": store.name, "offset": offset,
                         "rows": len(batch), "keys_hash": multiset_hash([key(a) for a, _, _ in batch]), "status": "started"}
                self.journal["batches"].append(event)
                atomic_json(self.journal_path, self.journal)
                store.write(batch)
                event["status"] = "completed"
                atomic_json(self.journal_path, self.journal)
                completed += 1
                if completed % 20 == 0:
                    print(json.dumps({"direction": direction, "store": store.name, "rows_done": offset + len(batch)}), flush=True)
                if fail_after_batches == completed:
                    raise RuntimeError("Injected interruption for rollback recovery test")
            require(multiset_hash(store.read()) == multiset_hash(targets[store.name]), "Destination postcondition mismatch; rollback required")
        self.journal["status"] = "applied" if direction == "apply" else "rolled_back"
        atomic_json(self.journal_path, self.journal)
        return {s.name: {"written_rows": len(pending[s.name]), "full_fingerprint": multiset_hash(s.read())} for s in self.stores}

    def close(self):
        self.lock.close()
