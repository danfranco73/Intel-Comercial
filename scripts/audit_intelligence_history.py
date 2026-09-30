"""Read persisted evidence only; never backfills or writes to a database/Chess."""
import argparse
import csv
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clickhouse_client import get_clickhouse_client, _qualified_table
from mongo_client import get_db
from sales_coach.domain.intelligence import now_iso
from sales_coach.repositories.product_identity_repository import ProductIdentityRepository
from sales_coach.services.sales_coverage_service import SalesCoverageService, calendar_days, day, observed_daily
from supabase_client import supabase_configured, supabase_sync_enabled


def audit(snapshot_id, start, end, output):
    if end < start:
        raise ValueError("El inicio no puede ser posterior al fin")
    db = get_db()
    snapshot = db.intelligence_stock_snapshots.find_one({"snapshot_id": snapshot_id})
    if not snapshot:
        raise ValueError("Snapshot no encontrado")
    output.mkdir(parents=True, exist_ok=True)
    report = {"generated_at": now_iso(), "requested_range": {"start": start.isoformat(), "end": end.isoformat()},
              "snapshot_id": snapshot_id, "sources": {}}
    for source in ("clickhouse", "mongo"):
        daily = observed_daily(db, source)
        coverage = SalesCoverageService(db, daily_loader=lambda *_: daily).assess(source, {"requested": (start, end)})["requested"]
        missing = [d for r in coverage["unverified_ranges"] for d in calendar_days(day(r["start"]), day(r["end"]))]
        missing = set(missing)
        monthly = Counter()
        for d, n in daily.items():
            monthly[d.strftime("%Y-%m")] += n
        dates = calendar_days(start, end)
        report["sources"][source] = {"rows": sum(daily.values()), "min_date": min(daily).isoformat() if daily else None,
            "max_date": max(daily).isoformat() if daily else None, "days_with_rows": len(daily),
            "months_with_rows": len(monthly), "monthly_rows": dict(sorted(monthly.items())), "coverage": coverage,
            "months_without_rows_in_requested_range": sorted({d.strftime("%Y-%m") for d in dates} - set(monthly)),
            "days_without_rows": [d.isoformat() for d in dates if not daily.get(d)]}
        with (output / f"{source}-daily-coverage.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "persisted_rows", "ingestion_coverage"])
            writer.writerows((d.isoformat(), daily.get(d, 0), "unverified" if d in missing else
                "confirmed_no_rows" if not daily.get(d) else "certified") for d in dates)
    c = get_clickhouse_client()
    table = _qualified_table()
    report["clickhouse_tables"] = c.query("SELECT database,name,engine FROM system.tables WHERE database NOT IN ('system','INFORMATION_SCHEMA','information_schema')").result_rows
    report["clickhouse_columns"] = [r[0] for r in c.query(f"DESCRIBE TABLE {table}").result_rows]
    sales_products = c.query(f"SELECT product_key,min(date),max(date),count() FROM {table} GROUP BY product_key").result_rows
    sale_keys = {r[0] for r in sales_products}
    snapshot_rows = list(db.intelligence_stock_rows.find({"snapshot_id": snapshot_id}, {"_id": 0, "raw": 0, "article_name": 0}))
    version_bindings = ProductIdentityRepository(db).bindings(snapshot.get("identity_version"))
    products = {}
    inconsistent = set()
    for r in snapshot_rows:
        key = r["physical_article_id"]
        binding = r.get("product_identity") or {"status": "unknown", "statistical_article_ids": []}
        if key in products and products[key] != binding:
            inconsistent.add(key)
        products[key] = binding
        if binding != version_bindings.get(key, {"version": snapshot.get("identity_version"), "status": "unknown", "statistical_article_ids": []}):
            inconsistent.add(key)
    def status(key):
        return "ambiguous" if key in inconsistent else products[key]["status"]
    def breakdown(ids):
        tally = Counter(status(k) for k in ids)
        return {"total": len(ids), **{s: tally[s] for s in ("verified", "ambiguous", "unknown")},
                "verified_pct": round(100 * tally["verified"] / len(ids), 4) if ids else None}
    nonzero = {r["physical_article_id"] for r in snapshot_rows if r["packs"] or r["units"]}
    verified = {k for k in products if status(k) == "verified"}
    with_history = {k for k in verified if products[k]["statistical_article_ids"][0] in sale_keys}
    report["mapping"] = {"identity_version": snapshot.get("identity_version"), "articles": breakdown(set(products)),
        "nonzero_stock_articles": breakdown(nonzero), "rows": len(snapshot_rows),
        "rows_by_status": dict(Counter(status(r["physical_article_id"]) for r in snapshot_rows)),
        "verified_articles_with_sales_history": len(with_history),
        "verified_articles_without_observed_sales_history": len(verified - with_history),
        "stock_articles_with_verified_mapping_and_sales_history_pct": round(100 * len(with_history) / len(products), 4),
        "unknown_ids_coinciding_with_sales_keys_not_certified": len({k for k in products if status(k) == "unknown"} & sale_keys),
        "physical_quantity_coverage_pct": None,
        "quantity_coverage_reason": "HETEROGENEOUS_PRODUCTS_AND_UNVERIFIED_UNITS",
        "by_deposit": {d: breakdown({r["physical_article_id"] for r in snapshot_rows if r["deposit_id"] == d})
                       for d in snapshot["expected_deposit_ids"]}}
    with (output / "snapshot-product-mapping.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["physical_article_id", "status", "statistical_article_ids", "observed_sales_history", "nonzero_stock"])
        writer.writerows((k, status(k), "|".join(products[k]["statistical_article_ids"]), k in with_history, k in nonzero)
                         for k in sorted(products, key=int))
    report["snapshot"] = {"status": snapshot["status"], "execution_coverage": snapshot["execution_coverage"],
        "stock_date": snapshot["requested_stock_date"], "captured_at": snapshot["finished_at"],
        "expected_deposits": snapshot["expected_deposit_ids"],
        "universe_coverage": snapshot["configuration"]["universe_coverage"],
        "validated_location_deposits": [d["deposit_id"] for d in snapshot["configuration"]["deposits"]
            if d.get("relationships_verified") and d["deposit_id"] in snapshot["expected_deposit_ids"]]}
    report["other_persistence"] = {"supabase_configured": supabase_configured(), "supabase_sync_enabled": supabase_sync_enabled(),
        "sessions": {"count": db.sessions.count_documents({}), "purpose": "dataset_configuration_and_planning"},
        "registros": {"count": db.registros.count_documents({}), "purpose": "analysis_summaries_not_sales_facts"},
        "local_files": {folder: [str(p.relative_to(ROOT)) for p in (ROOT / folder).rglob("*") if p.is_file()]
                        for folder in ("uploads", "data")}}
    report["sync_status_counts"] = dict(Counter(r["status"] for r in db.sync_runs.find({"entity": "sales"}, {"status": 1})))
    (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.snapshot_id, args.start, args.end, args.output)
    print(json.dumps({"sources": {s: {k: v for k, v in r.items() if k in {"rows", "min_date", "max_date", "days_with_rows", "months_with_rows"}}
        for s, r in result["sources"].items()}, "mapping": result["mapping"]}, ensure_ascii=False))
