"""Capture at most seven days directly from Chess, with no database writes.

Private immutable originals, two sequential passes, bounded requests/bytes/time,
and before/after database evidence. Never calls a sync or migration function.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_sales_phase1 import dates, require, row_fingerprint
from sales_coach.domain.sales_v2 import multiset_hash

MAX_REQUESTS = 40
MAX_SECONDS = 900
MAX_PAGE_BYTES = 64 * 1024 * 1024
MAX_DISK_BYTES = 200 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def validate_page(body, day, page, expected_pages=None):
    require(not body.get("error") and not body.get("Error"), "Chess application error")
    pagination = str(body.get("cantComprobantesVentas") or "")
    match = re.search(r"(?:obtenido:\s*)?(\d+)\s*/\s*(\d+).*?Cantidad de comprobantes totales:\s*(\d+)", pagination, re.I)
    require(match is not None, "Missing explicit pagination/document count")
    current, total, documents = map(int, match.groups())
    require((current == page and total >= page) or (page == 1 and documents == 0 and current in (0, 1)), "Invalid page number")
    require(expected_pages is None or total == expected_pages, "Pagination changed")
    envelope = body.get("dsReporteComprobantesApi")
    require(isinstance(envelope, dict), "Missing sales envelope")
    rows = envelope.get("VentasResumen")
    if rows is None and envelope == {} and documents == 0:
        rows = []
    require(isinstance(rows, list), "Invalid sales rows")
    require(bool(rows) or documents == 0, "Unconfirmed empty response")
    require(not rows or documents > 0, "Rows conflict with zero documents")
    require(all(r.get("fechaComprobate") == day for r in rows), "Row outside requested day")
    return rows, total, documents


def snapshot(root, label, start, end):
    from bson import json_util
    from clickhouse_client import get_clickhouse_client, _qualified_table, _config
    from mongo_client import get_db
    ch, db, cfg = get_clickhouse_client(), get_db(), _config()
    require(ch is not None and db is not None, "Both databases required")
    params = {"start": start, "end": end, "db": cfg["database"], "table": cfg["table"]}
    settings = {"readonly": 1, "max_execution_time": 30}
    pending = ch.query("SELECT count() FROM system.mutations WHERE database={db:String} AND table={table:String} AND NOT is_done",
                       parameters=params, settings=settings).result_rows[0][0]
    require(not pending, "ClickHouse mutation active")
    require(not db.scheduler_leases.find_one({"_id": "sales_scheduler", "expires_at": {"$gt": datetime.now(timezone.utc)}}, {"_id": 1}), "Scheduler active")
    rows = list(ch.query(f"SELECT * FROM {_qualified_table()} WHERE date BETWEEN {{start:Date}} AND {{end:Date}}",
                         parameters=params, settings=settings).named_results())
    mongo = list(db.erp_sales.find({"date": {"$gte": start, "$lte": end}}).max_time_ms(30000))
    for source, records in [("clickhouse", rows), ("mongo", mongo)]:
        path = root / f"{label}-{source}.jsonl.gz"
        with gzip.open(path, "xt") as stream:
            for row in records:
                stream.write((json_util.dumps(row) if source == "mongo" else json.dumps(row, default=str)) + "\n")
    schema = ch.query(f"DESCRIBE TABLE {_qualified_table()}", settings=settings).result_rows
    write_json(root / f"{label}-schema.json", schema)
    return {"captured_at": now(), "clickhouse_rows": len(rows), "mongo_rows": len(mongo),
            "clickhouse_fingerprint": row_fingerprint(rows), "mongo_fingerprint": row_fingerprint(mongo),
            "clickhouse_full_fingerprint": multiset_hash(rows), "mongo_full_fingerprint": multiset_hash(mongo)}


def capture(root, start, end):
    days = dates(start, end)
    require(root.is_relative_to(ROOT / "artifacts"), "Capture must be private under artifacts/")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    plan = {"range": {"start": start, "end": end}, "sales_get_budget": MAX_REQUESTS,
            "authentication_budget": 1, "parallel_requests": 1, "retries": 0,
            "wall_seconds_budget": MAX_SECONDS, "disk_bytes_budget": MAX_DISK_BYTES,
            "max_page_bytes": MAX_PAGE_BYTES, "production_writes": False,
            "started_at": now(), "timezone": "America/Argentina/Cordoba"}
    write_json(root / "plan.json", plan)
    tick, requests, days_result = time.monotonic(), 0, []
    before = snapshot(root, "before", start, end)
    write_json(root / "before.json", before)
    require(before["clickhouse_fingerprint"] == before["mongo_fingerprint"], "Sources differ before capture")
    from erp_client import erp_login, get_erp_config
    os.environ["CHESS_ERP_RETRIES"] = "1"
    os.environ["CHESS_ERP_TIMEOUT"] = "60"
    cookie = erp_login()["cookie"]
    cfg = get_erp_config()
    source_hash = hashlib.sha256(cfg["base_url"].encode()).hexdigest()
    context = None if cfg["verify_ssl"] else ssl._create_unverified_context()
    try:
        for day in days:
            passes = []
            for pass_no in (1, 2):
                records, pages, page, total, doc_count = [], [], 1, None, None
                while True:
                    require(requests < MAX_REQUESTS, "Request budget exhausted")
                    require(time.monotonic() - tick < MAX_SECONDS, "Wall time budget exhausted")
                    require(sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) < MAX_DISK_BYTES - MAX_PAGE_BYTES,
                            "Insufficient local evidence budget")
                    params = {"fechaDesde": day, "fechaHasta": day, "nroLote": page, "detallado": "true"}
                    request = Request(cfg["base_url"] + "/ventas/?" + urlencode(params), method="GET",
                                      headers={"Accept": "application/json", "Cookie": cookie})
                    started, request_tick = now(), time.monotonic()
                    requests += 1
                    with urlopen(request, timeout=60, context=context) as response:
                        payload = response.read(MAX_PAGE_BYTES + 1)
                        status = response.status
                    elapsed = time.monotonic() - request_tick
                    require(len(payload) <= MAX_PAGE_BYTES, "Response exceeds size budget")
                    path = root / f"{day}-pass{pass_no}-page{page}.json.gz"
                    with gzip.open(path, "xb") as stream:
                        stream.write(payload)
                    meta = {"path": path.name, "sha256": hashlib.sha256(payload).hexdigest(),
                            "started_at": started, "finished_at": now(), "seconds": elapsed,
                            "status": status, "bytes": len(payload), "page": page}
                    write_json(root / (path.name + ".meta.json"), meta)
                    require(status == 200 and elapsed <= 60, "Error or abnormal response latency")
                    require(time.monotonic() - tick <= MAX_SECONDS, "Wall time budget exhausted")
                    rows, total, docs = validate_page(json.loads(payload), day, page, total)
                    require(doc_count is None or docs == doc_count, "Document count changed between pages")
                    doc_count = docs
                    records.extend(rows)
                    pages.append(meta)
                    if docs == 0 or page >= total:
                        break
                    page += 1
                    time.sleep(0.5)
                passes.append({"pages": pages, "rows": len(records), "digest": multiset_hash(records),
                               "reported_documents": doc_count, "explicit_empty": doc_count == 0})
                print(json.dumps({"day": day, "pass": pass_no, "rows": len(records), "requests_used": requests}), flush=True)
            require(passes[0]["digest"] == passes[1]["digest"] and passes[0]["reported_documents"] == passes[1]["reported_documents"],
                    "Source changed between passes")
            result = {"day": day, "status": "source_verified", "passes": passes}
            write_json(root / f"{day}-verified.json", result)
            days_result.append(result)
        after = snapshot(root, "after", start, end)
        write_json(root / "after.json", after)
        require(all(before[k] == after[k] for k in ["mongo_full_fingerprint", "clickhouse_full_fingerprint"]), "Databases changed during capture")
        manifest = {**plan, "status": "source_verified_databases_unchanged", "finished_at": now(),
                    "source_instance_sha256": source_hash, "sales_requests": requests,
                    "seconds": time.monotonic() - tick, "days": days_result,
                    "before": before, "after": after, "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        write_json(root / "manifest.json", manifest)
        print(json.dumps({"status": manifest["status"], "sales_requests": requests, "seconds": manifest["seconds"]}), flush=True)
    except Exception as exc:
        write_json(root / "failure.json", {"status": "failed_not_published", "finished_at": now(),
                                          "error_type": type(exc).__name__, "sales_requests": requests})
        raise RuntimeError("Capture stopped; private evidence retained, no production writes") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    capture(args.output.resolve(), args.start, args.end)
