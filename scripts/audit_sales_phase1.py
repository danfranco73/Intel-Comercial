"""Bounded, read-only reconciliation of archived Chess evidence and current V1.

No Chess requests, schema initialization, repairs or synchronization. Output is a
new private JSON artifact. A successful audit is not approval to repair V1.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clickhouse_client import _compact_records
from erp_client import normalize_erp_sale_row as normalize_current_sale_row
from sales_coach.domain.sales_v2 import LEGACY_KEY, METRICS, digest, multiset_hash


def normalize_erp_sale_row(row):
    """Replay the captured pre-repair V1 mapping, independently of new-load fixes.

    V1 ignored Chess `internos`. Keep that historical projection explicit so
    audit evidence remains reproducible; the repair derives tax from raw source.
    """
    return normalize_current_sale_row({k: v for k, v in row.items() if k != "internos"})

ZERO = Decimal(0)
MONEY = tuple(f for f in METRICS if f != "quantity")
COMPARE_FIELDS = (*LEGACY_KEY, "company_key", "supplier_key", "business_type_key", *METRICS)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    require(value is not None and value != "" and not isinstance(value, bool), "Missing numeric evidence")
    result = Decimal(str(value))
    require(result.is_finite(), "Nonfinite numeric evidence")
    return result


def dates(start, end):
    left, right = date.fromisoformat(start), date.fromisoformat(end)
    require(0 <= (right - left).days < 7, "Audit must cover between one and seven days")
    return [(left + timedelta(days=i)).isoformat() for i in range((right - left).days + 1)]


def evidence_path(root, relative):
    target = (root / relative).resolve()
    require(target.is_relative_to(root.resolve()), "Evidence path escapes archive")
    return target


def row_fingerprint(rows):
    normalized = []
    for row in rows:
        normalized.append({f: str(number(row[f]).normalize()) if f in METRICS else str(row.get(f) or "")
                           for f in COMPARE_FIELDS})
    return multiset_hash(normalized)


def grouped_money(rows):
    groups = defaultdict(lambda: {f: ZERO for f in MONEY})
    for row in rows:
        key = (str(row["date"]), str(row["company_key"]), str(row["invoice"]).split("-")[0])
        for field in MONEY:
            groups[key][field] += number(row[field])
    return groups


def reconcile(rows, raw):
    """Reproduce normalization and test the monetary bridge per day/company/type.

    Does not infer that any matching aggregate authorizes a row-level repair.
    Missing numeric fields are errors, never substituted with zero.
    """
    included, excluded, bonuses = [], [], []
    for original in raw:
        normalized = normalize_erp_sale_row(original)
        if normalized is None:
            require(original.get("anulado") == "SI", "Unexpected normalization exclusion")
            excluded.append(original)
            continue
        require(number(original.get("subtotalNeto")) == number(normalized["amount_net"]),
                "Normalizer differs from explicit source net")
        require(number(original.get("subtotalFinal")) == number(normalized["amount_final"]),
                "Normalizer differs from explicit source final")
        included.append(normalized)
        if number(original["subtotalNeto"]) == 0 and number(original.get("subtotalBruto")) != 0:
            require(number(original.get("bonificacion")) == 100, "Zero-net line is not a verified full bonus")
            require(number(original["subtotalFinal"]) == 0, "Full bonus has nonzero final")
            require(number(original.get("subtotalBonificado")) == number(original["subtotalBruto"]),
                    "Discount differs from gross")
            require(number(original.get("cantidadesCorCargo")) == 0, "Full bonus has charged quantity")
            require(number(original.get("cantidadesSinCargo")) == number(original.get("cantidadesTotal")),
                    "Full bonus quantities do not reconcile")
            bonuses.append((normalized, original))
    candidate = _compact_records(included)
    before, after, source = grouped_money(rows), grouped_money(candidate), grouped_money(included)
    require(after == source, "Compaction changed monetary totals")
    bonus_by_group = defaultdict(lambda: ZERO)
    for normalized, original in bonuses:
        key = (str(normalized["date"]), normalized["company_key"], normalized["invoice"].split("-")[0])
        bonus_by_group[key] -= number(original["subtotalBruto"])
    bridge = []
    for key in sorted(before.keys() | after.keys()):
        a = before.get(key, {f: ZERO for f in MONEY})
        b = after.get(key, {f: ZERO for f in MONEY})
        delta = {f: b[f] - a[f] for f in MONEY}
        expected = bonus_by_group[key]
        residual = {f: delta[f] - (ZERO if f == "internal_taxes" else expected) for f in MONEY}
        bridge.append({"date": key[0], "company_key": key[1], "document_type": key[2],
                       "v1": a, "replayed_source": b, "delta": delta,
                       "expected_bonus_delta": expected, "unexplained": residual})
    keys = lambda records: Counter(tuple(str(r.get(k) or "") for k in (*LEGACY_KEY, "company_key")) for r in records)
    akeys, bkeys = keys(rows), keys(candidate)
    require(all(v == 1 for v in akeys.values()), "Duplicate current V1 keys")
    require(all(v == 1 for v in bkeys.values()), "Duplicate replay keys")
    return {
        "all_monetary_groups_explained": all(v == 0 for g in bridge for v in g["unexplained"].values()),
        "raw_rows": len(raw), "excluded_cancelled_rows": len(excluded), "v1_rows": len(rows),
        "replayed_compact_rows": len(candidate), "bonus_lines": len(bonuses),
        "bonus_returns": sum(number(r["subtotalBruto"]) < 0 for _, r in bonuses),
        "net_v1": sum((number(r["amount_net"]) for r in rows), ZERO),
        "net_replayed_source": sum((number(r["amount_net"]) for r in candidate), ZERO),
        "explained_net_delta": sum(bonus_by_group.values(), ZERO),
        "keys_only_v1": len(akeys.keys() - bkeys.keys()),
        "keys_only_replay": len(bkeys.keys() - akeys.keys()),
        "bridge": bridge,
        "scope_limit": "Aggregate monetary proof; dimensional changes and zero aggregates require separate row-level review.",
    }


def load_archives(root, days):
    meta = json.loads((root / "baseline-meta.json").read_text())
    baseline_path = root / "baseline.jsonl.gz"
    require(hashlib.sha256(baseline_path.read_bytes()).hexdigest() == meta["sha256"], "Baseline checksum mismatch")
    release = json.loads((root / "candidate.json").read_text())
    entries = [json.loads(s) for s in evidence_path(root, f"releases/{release['release_id']}/days.jsonl").read_text().splitlines()]
    require(digest(entries) == release["manifest_sha256"], "Release manifest mismatch")
    require(release["baseline_sha256"] == meta["sha256"], "Release references another baseline")
    by_day = {r["date"]: r for r in entries}
    baseline = []
    with gzip.open(baseline_path, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            if row["date"] in days:
                baseline.append(row)
    raw, source_evidence = [], []
    for day in days:
        entry = json.loads((root / "days" / f"{day}.json").read_text())
        selected = json.loads((root / "selected" / f"{day}.json").read_text())
        require(entry["status"] == "source_verified", "Unverified source day")
        require(by_day[day]["load_id"] == selected["load_id"], "Selected release differs")
        require(selected["capture_id"] == entry["capture_id"] and selected["raw_roundtrip_verified"], "Capture mismatch")
        require(len(entry["passes"]) == 2, "Two source passes required")
        passes = []
        for capture in entry["passes"]:
            page_rows = []
            for page in capture["pages"]:
                require(page["success"] and page["http_status"] == 200, "Unsuccessful archived page")
                body = gzip.open(evidence_path(root, page["path"]), "rb").read()
                require(hashlib.sha256(body).hexdigest() == page["sha256"], "Page checksum mismatch")
                page_rows.extend(json.loads(body)["dsReporteComprobantesApi"].get("VentasResumen", []))
            require(len(page_rows) == capture["rows"], "Source row count mismatch")
            require(multiset_hash(page_rows) == capture["digest"] == selected["source_digest"], "Source digest mismatch")
            passes.append(page_rows)
        raw.extend(passes[0])
        source_evidence.append({"day": day, "captured_at": entry["started_at"],
                                "rows": len(passes[0]), "source_digest": selected["source_digest"]})
    return baseline, raw, {"baseline_sha256": meta["sha256"], "baseline_captured_at": meta["captured_at"],
                           "release_manifest_sha256": release["manifest_sha256"], "days": source_evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    days = dates(args.start, args.end)
    output = args.output.resolve()
    require(output.is_relative_to(ROOT / "artifacts"), "Private output must be under artifacts/")
    require(not output.exists(), "Refusing to overwrite an existing artifact")
    baseline, raw, evidence = load_archives(args.archive.expanduser().resolve(), days)

    # Direct read clients only: do not invoke legacy loaders or schema helpers.
    from clickhouse_client import get_clickhouse_client, _qualified_table, _config
    from mongo_client import get_db
    ch, db, cfg = get_clickhouse_client(), get_db(), _config()
    require(ch is not None and db is not None, "Both configured sources are required")
    settings = {"readonly": 1, "max_execution_time": 30}
    params = {"start": args.start, "end": args.end, "db": cfg["database"], "table": cfg["table"]}

    def idle():
        pending = ch.query("SELECT count() FROM system.mutations WHERE database={db:String} AND table={table:String} AND NOT is_done",
                           parameters=params, settings=settings).result_rows[0][0]
        lease = db.scheduler_leases.find_one({"_id": "sales_scheduler", "expires_at": {"$gt": datetime.now(timezone.utc)}}, {"_id": 1})
        require(not pending and not lease, "Active mutation or scheduler: retry when idle")

    idle()
    columns = ",".join(COMPARE_FIELDS)
    query = f"SELECT {columns} FROM {_qualified_table()} WHERE date BETWEEN {{start:Date}} AND {{end:Date}}"
    current = list(ch.query(query, parameters=params, settings=settings).named_results())
    query_mongo = {"date": {"$gte": args.start, "$lte": args.end}}
    mongo = list(db.erp_sales.find(query_mongo, {"_id": 0, **{k: 1 for k in COMPARE_FIELDS}}).max_time_ms(30000))
    signature = row_fingerprint(current)
    baseline_matches = signature == row_fingerprint(baseline)
    mongo_matches = signature == row_fingerprint(mongo)
    result = reconcile(current, raw)
    repeated = list(ch.query(query, parameters=params, settings=settings).named_results())
    repeated_mongo = list(db.erp_sales.find(query_mongo, {"_id": 0, **{k: 1 for k in COMPARE_FIELDS}}).max_time_ms(30000))
    idle()
    stable = signature == row_fingerprint(repeated) and row_fingerprint(mongo) == row_fingerprint(repeated_mongo)
    checks = {"current_matches_frozen_baseline": baseline_matches,
              "mongo_matches_clickhouse_for_entire_requested_window": mongo_matches,
              "read_fingerprints_stable": stable,
              "monetary_bridge_explained": result["all_monetary_groups_explained"]}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "range": {"start": args.start, "end": args.end},
              "status": "reproduced_not_repaired" if all(checks.values()) else "needs_investigation",
              "production_writes": False, "chess_requests": 0, "checks": checks,
              "evidence": evidence, "current_v1_fingerprint": signature, "reconciliation": result,
              "code_sha256": {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in
                              ["erp_client.py", "clickhouse_client.py", "scripts/audit_sales_phase1.py"]},
              "limitations": ["Archived Chess capture is not current ERP data.",
                              "Stable repeated reads do not provide a cross-database transaction.",
                              "No repair candidate is authorized by aggregate reconciliation.",
                              "No conclusion extends beyond the requested window."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"status": report["status"], "checks": checks, "artifact": str(output)}, ensure_ascii=False))
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
