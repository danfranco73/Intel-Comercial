"""Offline simulation from SELECT queries; no database writes or activation."""
import argparse
import csv
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clickhouse_client import get_clickhouse_client, _qualified_table
from sales_coach.domain.deposit_attribution import DepositAttributionEngine
from sales_coach.domain.intelligence import fingerprint, now_iso
from sales_coach.repositories.attribution_rule_repository import AttributionRuleRepository


def bucket():
    return {"records": 0, "net_amount": 0.0, "positive_amount": 0.0, "negative_amount": 0.0,
            "absolute_amount": 0.0, "commercial_quantity_unverified": 0.0}


def increment(target, values):
    for field in target:
        target[field] += values[field]


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        path.write_text("")
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(rule_directory, version, output):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Audit output is immutable: use a new empty directory")
    release = AttributionRuleRepository(rule_directory).load_release(version)
    engine = DepositAttributionEngine(release["rules"])
    output.mkdir(parents=True, exist_ok=True)
    client, table = get_clickhouse_client(), _qualified_table()
    columns = [r[0] for r in client.query(f"DESCRIBE TABLE {table}").result_rows]
    # There is no canonical route_key in this storage. Do not parse load sheets
    # or apply the current customer portfolio to past invoices.
    required = {"date", "company_key", "company_name", "sales_scheme_key", "sales_scheme_name", "channel", "route_description"}
    if not required <= set(columns):
        raise ValueError("Missing source dimensions")
    for r in release["rules"]:
        if any(k in r["product_scope"] for k in ("product", "supplier")):
            raise ValueError("This projection must be extended before simulating product/supplier-specific releases")
    projection = "date, company_key, company_name, sales_scheme_key, sales_scheme_name, channel"
    route_expression = "multiIf(route_description='', 'MISSING', startsWith(route_description,'0000'), 'LOAD_SHEET', 'UNVERIFIED_DESCRIPTION')"
    sql = f"""SELECT {projection}, {route_expression} AS route_evidence,
        count() AS records, sum(amount_net) AS net_amount,
        sumIf(amount_net, amount_net > 0) AS positive_amount,
        sumIf(amount_net, amount_net < 0) AS negative_amount,
        sum(abs(amount_net)) AS absolute_amount, sum(quantity) AS commercial_quantity_unverified
        FROM {table} GROUP BY {projection}, route_evidence ORDER BY {projection}, route_evidence"""
    # Counts preserve the number of underlying persisted facts. Grouping only
    # merges rows with identical inputs for every predicate used by this release.
    source_check_sql = f"SELECT count(),min(date),max(date),sum(amount_net),sum(abs(amount_net)),max(synced_at),uniqExact(sync_run_id) FROM {table}"
    before = client.query(source_check_sql).result_rows[0]
    status = defaultdict(bucket)
    deposits = {d: bucket() for d in ("1", "4", "6", "7", "8", "20", "22", "23")}
    groups, raw_kinds, reasons, rules_matched = defaultdict(bucket), defaultdict(bucket), defaultdict(bucket), defaultdict(bucket)
    monthly = defaultdict(bucket)
    source_groups = 0
    with gzip.open(output / "decisions.jsonl.gz", "wt", encoding="utf-8") as ledger:
        with client.query_rows_stream(sql) as stream:
            for values in stream:
                when, company, company_name, force, force_name, channel, route_kind, *measures = values
                metrics = dict(zip(bucket(), measures))
                fact = {"date": when.isoformat(), "billing_company": company or None,
                    "sales_force": force or None, "route": None, "channel": channel or None,
                    "family": None, "line": None, "storage_class": None}
                decision = engine.evaluate(fact)
                state, depot = decision["status"], decision["deposit_id"]
                increment(status[state], metrics)
                if depot:
                    deposits.setdefault(depot, bucket())
                    increment(deposits[depot], metrics)
                key = (company, company_name, force, force_name, channel, "UNKNOWN", state, depot or "", decision["reason"])
                increment(groups[key], metrics)
                increment(raw_kinds[route_kind], metrics)
                increment(reasons[decision["reason"]], metrics)
                increment(monthly[(when.strftime("%Y-%m"), state)], metrics)
                for matched in decision["matched_rules"]:
                    increment(rules_matched[(matched["rule_id"], state)], metrics)
                entry = {"source_group_id": fingerprint([fact, route_kind, company_name, force_name]),
                    "input": fact, "route_evidence": route_kind, "metrics": metrics, "decision": decision}
                ledger.write(json.dumps(entry, ensure_ascii=False) + "\n")
                source_groups += 1
    after = client.query(source_check_sql).result_rows[0]
    if before != after:
        raise RuntimeError("Source changed during audit; discard this run and repeat")
    total = bucket()
    for values in status.values():
        increment(total, values)
    if total["records"] != before[0] or not math.isclose(total["net_amount"], before[3], rel_tol=1e-12, abs_tol=.05):
        raise RuntimeError("Simulation totals do not reconcile with source")
    attributed = status.get("ATTRIBUTED", bucket())
    for state in ("ATTRIBUTED", "UNATTRIBUTED", "CONFLICT"):
        status.setdefault(state, bucket())
    group_rows = [{**dict(zip(("company_key", "company", "force_key", "force", "channel", "commercial_route", "status", "deposit_id", "reason"), k)), **v}
                  for k, v in groups.items()]
    top = sorted([r for r in group_rows if r["status"] == "UNATTRIBUTED"], key=lambda r: -r["net_amount"])[:20]
    dimension_rows = []
    for dimension in ("company", "force", "channel", "commercial_route"):
        values = defaultdict(bucket)
        for r in group_rows:
            increment(values[(r[dimension], r["status"], r["deposit_id"])], {k: r[k] for k in bucket()})
        dimension_rows.extend({"dimension": dimension, "value": k[0], "status": k[1], "deposit_id": k[2], **v} for k, v in values.items())
    route_checks = [{"route": route, "expected_deposit": depot, "historical_verifiable_records": 0,
        "result": "NOT_VERIFIABLE_CANONICAL_ROUTE_ABSENT"} for route, depot in (
        ("100", "4"), ("200", "6"), ("201", "7"), ("202", "8"), ("203", "22"), ("204", "23"), ("205", "23"))]
    report = {"audit_id": output.name, "generated_at": now_iso(), "mode": "simulation_only", "production_active": False,
        "rule_release": release["version"], "ruleset_hash": engine.ruleset_hash, "release_hash": fingerprint(release),
        "source": {"table": table, "min_date": str(before[1]), "max_date": str(before[2]), "rows": before[0],
            "columns": columns, "max_synced_at": str(before[5]), "distinct_sync_ids": before[6],
            "stable_before_after": True, "predicate_groups_evaluated": source_groups},
        "totals": total, "status": dict(status), "by_deposit": deposits,
        "attribution_pct": {"records": attributed["records"] / total["records"] * 100 if total["records"] else None,
            "net_billing": attributed["net_amount"] / total["net_amount"] * 100 if total["net_amount"] else None,
            "absolute_billing": attributed["absolute_amount"] / total["absolute_amount"] * 100 if total["absolute_amount"] else None,
            "physical_quantity_or_packs": None, "quantity_reason": "COMMERCIAL_QUANTITY_UNIT_UNVERIFIED"},
        "billing_basis": "amount_net nominal; credits retain their negative sign; currency not certified",
        "route_evidence": dict(raw_kinds), "reasons": dict(reasons), "top20_unattributed": top,
        "special_route_checks": route_checks,
        "matched_rules": [{"rule_id": k[0], "decision_status": k[1], **v} for k, v in rules_matched.items()]}
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (output / "rules-used.json").write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n")
    (output / "source-query.sql").write_text(sql + ";\n")
    write_csv(output / "by-company-force-channel-route.csv", sorted(group_rows, key=lambda r: -r["net_amount"]))
    write_csv(output / "by-dimension.csv", dimension_rows)
    write_csv(output / "by-deposit.csv", [{"deposit_id": d, **v} for d, v in deposits.items()])
    write_csv(output / "by-month.csv", [{"month": k[0], "status": k[1], **v} for k, v in sorted(monthly.items())])
    write_csv(output / "top20-unattributed.csv", top)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.rules, args.version, args.output)
    print(json.dumps({"rows": report["source"]["rows"], "status": report["status"], "percentages": report["attribution_pct"]}, ensure_ascii=False))
