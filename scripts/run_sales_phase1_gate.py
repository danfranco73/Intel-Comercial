"""Actual database gate using saved Chess/DB evidence. No production connector.

Usage: .venv/bin/python scripts/run_sales_phase1_gate.py --output artifacts/<new-dir>
Requires installed mongod and the existing native ClickHouse binary; no downloads.
"""
import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Do not load .env indirectly through the existing normalizer/audit helpers.
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
for variable in list(os.environ):
    if variable.startswith(("MONGO", "CLICKHOUSE", "CHESS_ERP")):
        os.environ.pop(variable)
# Defense in depth: this process cannot connect to or resolve an external host.
_connect, _resolve = socket.socket.connect, socket.getaddrinfo
def local_connect(sock, address):
    if sock.family != socket.AF_UNIX and address[0] != "127.0.0.1":
        raise RuntimeError("Gate permits loopback connections only")
    return _connect(sock, address)
def local_resolve(host, *args, **kwargs):
    if host not in (None, "127.0.0.1"):
        raise RuntimeError("Gate prohibits external DNS")
    return _resolve(host, *args, **kwargs)
socket.socket.connect, socket.getaddrinfo = local_connect, local_resolve

import clickhouse_connect
from bson import json_util
from pymongo import MongoClient
from sales_coach.services.sales_phase1_repair import (
    START, END, PROPOSAL_SHA, PATCH_FIELDS, RepairExecutor, MongoStore, ClickHouseStore,
    atomic_json, expected_rows, in_scope, key, number, require, row_fingerprint, multiset_hash)
from scripts.analyze_sales_phase1 import analyze, total, doc
from scripts.capture_sales_phase1 import validate_page


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(process, port):
    for _ in range(100):
        require(process.poll() is None, "Isolated database failed; inspect process log")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError("Isolated database startup timeout")


def read_ch(client, table, outside=False):
    suffix = f" WHERE date < '{START}' OR date > '{END}'" if outside else ""
    r = client.query(f"SELECT * FROM {table}" + suffix)
    return [dict(zip(r.column_names, row)) for row in r.result_rows]


def verify_source(capture, manifest, original, changes):
    raw, reported = [], {}
    for day in manifest["days"]:
        passes = []
        for run in day["passes"]:
            rows, expected = [], None
            for page in run["pages"]:
                path = (capture / page["path"]).resolve()
                require(path.is_relative_to(capture), "Source path escapes capture")
                body = gzip.open(path, "rb").read()
                require(hashlib.sha256(body).hexdigest() == page["sha256"], "Source page checksum mismatch")
                rr, expected, documents = validate_page(json.loads(body), day["day"], page["page"], expected)
                require(documents == run["reported_documents"], "Source document metadata mismatch")
                rows.extend(rr)
            require(len(rows) == run["rows"] and multiset_hash(rows) == run["digest"], "Source pass mismatch")
            passes.append(rows)
        require(len(passes) == 2 and multiset_hash(passes[0]) == multiset_hash(passes[1]), "Two source passes required")
        raw.extend(passes[0])
        reported[day["day"]] = day["passes"][0]["reported_documents"]
    report, recomputed = analyze(original, raw, reported)
    # Canonical encoding renders Decimal/string identically for saved proposals.
    require(multiset_hash(recomputed) == multiset_hash(changes), "Reviewed proposal cannot be reproduced from Chess")
    require(report["counts"]["counts_explained"] and not report["unexplained_row_differences"], "Unexplained source differences")
    source = report["contract"]["source_fields"]
    expected = {"amount": source["subtotalFinal"]["sum_included"],
                "amount_final": source["subtotalFinal"]["sum_included"],
                "amount_net": source["subtotalNeto"]["sum_included"],
                "internal_taxes": source["internos"]["sum_included"]}
    expected["amount_net_internal"] = expected["amount_net"] + expected["internal_taxes"]
    return report, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=ROOT / "artifacts/phase1-live-2026-10-05")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture, output = args.capture.resolve(), args.output.resolve()
    require(output.is_relative_to(ROOT / "artifacts"), "Private artifact destination required")
    output.mkdir(mode=0o700)  # Never overwrite a previous gate.
    os.umask(0o077)
    manifest = json.loads((capture / "manifest.json").read_text())
    require(manifest["status"] == "source_verified_databases_unchanged", "Incomplete source capture")
    originals = {
        "clickhouse": [json.loads(s) for s in gzip.open(capture / "before-clickhouse.jsonl.gz", "rt")],
        "mongo": [json_util.loads(s) for s in gzip.open(capture / "before-mongo.jsonl.gz", "rt")]}
    for name, rows in originals.items():
        require(multiset_hash(rows) == manifest["before"][name + "_full_fingerprint"], "Backup fingerprint mismatch")
        require(len(rows) == 35526 and all(in_scope(r) for r in rows), "Unexpected snapshot scope")
    proposal = capture / "reconciliation-final-repair-proposal.jsonl.gz"
    require(hashlib.sha256(proposal.read_bytes()).hexdigest() == PROPOSAL_SHA, "Unapproved proposal")
    changes = [json.loads(s) for s in gzip.open(proposal, "rt")]
    for rows in originals.values():
        expected_rows(rows, changes)
    source_report, expected = verify_source(capture, manifest, originals["clickhouse"], changes)
    atomic_json(output / "source-reverified.json", source_report)
    # Actual archived September 21 rows prove an outside-period control without
    # manufacturing business data. No later actual rows exist in these archives.
    archive = Path.home() / ".local/share/intel-comercial-v2-pilot"
    baseline = archive / "baseline.jsonl.gz"
    metadata = json.loads((archive / "baseline-meta.json").read_text())
    require(hashlib.sha256(baseline.read_bytes()).hexdigest() == metadata["sha256"], "Outside-period archive checksum mismatch")
    data = gzip.open(baseline, "rb").read()
    controls = [json.loads(s) for s in data.splitlines() if json.loads(s)["date"] == "2026-09-21"]
    del data
    require(bool(controls), "Missing actual outside-period controls")
    mongo_controls = [{**r, "_id": "gate-control-" + str(i)} for i, r in enumerate(controls)]
    schema = json.loads((capture / "before-schema.json").read_text())
    columns = [r[0] for r in schema]
    binary = archive / "bin/clickhouse"
    require(binary.is_file() and shutil.which("mongod"), "Installed database engines required")
    ch_port, mongo_port = free_port(), free_port()
    require(ch_port != mongo_port, "Port collision; rerun in new output directory")
    (output / "mongo-data").mkdir()
    p = escape(str(output))
    config = f'''<clickhouse><logger><level>warning</level><log>{p}/clickhouse.log</log><errorlog>{p}/clickhouse.err</errorlog></logger><path>{p}/clickhouse-data/</path><tmp_path>{p}/tmp/</tmp_path><timezone>UTC</timezone><http_port>{ch_port}</http_port><listen_host>127.0.0.1</listen_host><max_thread_pool_size>256</max_thread_pool_size><background_pool_size>16</background_pool_size><background_schedule_pool_size>2</background_schedule_pool_size><mark_cache_size>10485760</mark_cache_size><users><default><password></password><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota></default></users><profiles><default><max_threads>2</max_threads></default></profiles><quotas><default/></quotas></clickhouse>'''
    (output / "config.xml").write_text(config)
    processes, logs, executor = [], [], None
    report = {"status": "RUNNING", "production_connections": False, "production_writes": False,
              "range": [START, END], "proposal_sha256": PROPOSAL_SHA,
              "currency": "ARS nominal", "zero_group_insertions": 0,
              "tolerance": {"money_decimal_of_stored_float": "0", "quantities_changed": 0},
              "outside_control": {"rows": len(controls), "date": "2026-09-21", "source": str(baseline),
                                  "source_sha256": metadata["sha256"], "synthetic_sales": False}}
    try:
        for name, command in (
            ("clickhouse", [str(binary), "server", "--config-file", str(output / "config.xml")]),
            ("mongo", [shutil.which("mongod"), "--dbpath", str(output / "mongo-data"), "--bind_ip", "127.0.0.1",
                       "--port", str(mongo_port), "--wiredTigerCacheSizeGB", "0.25", "--nounixsocket"])):
            log = (output / (name + "-process.log")).open("w")
            logs.append(log)
            processes.append(subprocess.Popen(command, stdout=log, stderr=log))
        wait_port(processes[0], ch_port)
        wait_port(processes[1], mongo_port)
        ch = clickhouse_connect.get_client(host="127.0.0.1", port=ch_port, username="default", password="",
                                          autogenerate_session_id=False, send_receive_timeout=120)
        mongo = MongoClient("127.0.0.1", mongo_port, serverSelectionTimeoutMS=3000)
        collection = mongo["phase1_gate"]["sales"]
        ch.command("CREATE DATABASE phase1_gate")
        table = "phase1_gate.fact_sales_compact"
        definitions = [f"{r[0]} {r[1]}" + (f" {r[2]} {r[3]}" if r[2] else "") for r in schema]
        ch.command(f"CREATE TABLE {table} (" + ",".join(definitions) +
                   ") ENGINE=MergeTree PARTITION BY toYYYYMM(date) ORDER BY (date, client_key, seller_key, product_key, invoice)")
        chrows = deepcopy(originals["clickhouse"] + controls)
        for row in chrows:
            row["date"] = date.fromisoformat(row["date"])
            # Captured ClickHouse DateTime is UTC without an offset. Naive
            # datetime.timestamp() would otherwise reinterpret it as Argentina.
            row["synced_at"] = datetime.fromisoformat(row["synced_at"]).replace(tzinfo=timezone.utc)
        ch.insert(table, [[r[c] for c in columns] for r in chrows], column_names=columns)
        collection.insert_many(originals["mongo"] + mongo_controls, ordered=True)
        stores = [ClickHouseStore(ch, table), MongoStore(collection)]
        # Driver DateTime objects stringify differently if timezone-aware; values
        # returned by this version's native codec must reproduce saved fingerprints.
        before = {s.name: multiset_hash(s.read()) for s in stores}
        require(all(before[n] == manifest["before"][n + "_full_fingerprint"] for n in before), "Clone differs from preserved fingerprint")
        outside = lambda: {"clickhouse": multiset_hash(read_ch(ch, table, outside=True)),
                           "mongo": multiset_hash(list(collection.find({"date": {"$lt": START}})))}
        outside_before = outside()
        report.update(initial=before, engines={"clickhouse": ch.server_version, "mongo": mongo.server_info()["version"]},
                      production_engine_difference="Local MergeTree vs production SharedMergeTree; cloud replication not exercised")
        print(json.dumps({"status": "clones_verified", "rows_each": 35526, "distinct_repair_rows": len(changes), "control_rows": len(controls)}), flush=True)
        executor = RepairExecutor(stores, originals, changes, output / "executor")
        report["apply"] = executor.execute("apply")
        after = {s.name: s.read() for s in stores}
        report["reconciliation"] = {}
        for name, rows in after.items():
            totals = {f: total(rows, f) for f in PATCH_FIELDS}
            require(totals == expected, "Post-repair Chess monetary reconciliation failed")
            require(row_fingerprint(rows) == row_fingerprint(after["clickhouse"]), "Cross-database economic mismatch")
            original = {key(r): r for r in originals[name]}
            changed = [r for r in rows if multiset_hash([r]) != multiset_hash([original[key(r)]])]
            require(len(changed) == 17805, "Unexpected number of actually changed rows")
            require(all(multiset_hash([{k:v for k,v in r.items() if k not in PATCH_FIELDS}]) ==
                        multiset_hash([{k:v for k,v in original[key(r)].items() if k not in PATCH_FIELDS}])
                        for r in rows), "Unauthorized field modified")
            report["reconciliation"][name] = {"totals": totals, "delta_against_chess": {f: totals[f]-expected[f] for f in totals},
                "rows": len(rows), "documents": len({doc(r) for r in rows}), "actually_changed_distinct_rows": len(changed),
                "economic_fingerprint": row_fingerprint(rows), "unauthorized_field_changes": 0}
        require(outside() == outside_before, "Outside-period rows changed")
        report["outside_after_apply"] = outside()
        report["rollback"] = executor.execute("rollback")
        require(all(report["rollback"][n]["full_fingerprint"] == before[n] for n in before), "Rollback hash mismatch")
        require(outside() == outside_before, "Outside-period rollback drift")
        executor.close()
        executor = None
        # Exercise a real interrupted batch and recover with a fresh executor
        # loading its own destination-specific backups, exactly as a restart would.
        executor = RepairExecutor(stores, originals, changes, output / "interrupted-executor")
        try:
            executor.execute("apply", fail_after_batches=1)
            raise AssertionError("Fault injection did not interrupt")
        except RuntimeError as exc:
            require(str(exc).startswith("Injected interruption"), "Unexpected interruption")
        executor.close()
        backups = {name: [json_util.loads(s) for s in gzip.open(output / "interrupted-executor" / (name + "-backup.jsonl.gz"), "rt")]
                   for name in originals}
        executor = RepairExecutor(stores, backups, changes, output / "interrupted-executor", resume=True)
        report["interrupted_recovery"] = executor.execute("rollback")
        require(all(multiset_hash(s.read()) == before[s.name] for s in stores), "Restart rollback failed")
        require(outside() == outside_before, "Restart changed outside period")
        report.update(status="PASS_ISOLATED_ONLY_STOP_BEFORE_PRODUCTION", outside_initial=outside_before,
                      outside_final=outside(), original_backup_types_restored=True,
                      source_counts=source_report["counts"], no_production_authorization=True)
    except Exception as exc:
        report.update(status="FAILED", error_type=type(exc).__name__, error=str(exc))
        if executor and executor.journal["status"] in ("applied", "apply_in_progress", "rollback_in_progress"):
            try:
                report["failure_rollback"] = executor.execute("rollback")
            except Exception as recovery:
                report["failure_rollback_error"] = str(recovery)
        raise
    finally:
        if executor:
            executor.close()
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        report["local_processes_stopped"] = all(p.poll() is not None for p in processes)
        report["code_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                 ("sales_coach/services/sales_phase1_repair.py", "scripts/run_sales_phase1_gate.py",
                                  "scripts/analyze_sales_phase1.py", "erp_client.py", "bi/facts.py")}
        report["artifact_bytes"] = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
        report["allocated_artifact_bytes"] = sum(p.stat().st_blocks * 512 for p in output.rglob("*") if p.is_file())
        atomic_json(output / "gate-report.json", report)
    print(json.dumps({"status": report["status"], "report": str(output / "gate-report.json")}), flush=True)


if __name__ == "__main__":
    main()
