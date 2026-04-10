from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

from erp_client import erp_login, fetch_sales_dataset
from mongo_client import (
    ERP_SALES_COLLECTION,
    ERP_SYNC_COLLECTION,
    _drop_index_if_exists,
    _ensure_erp_indexes,
    _sales_compact_mode,
    get_db,
    sync_erp_sales,
)


def parse_date(value: str) -> date:
    return date.fromisoformat(str(value))


def iter_chunks(start: date, end: date, chunk_days: int):
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        yield cursor.isoformat(), chunk_end.isoformat()
        cursor = chunk_end + timedelta(days=1)


def purge_sales(db, reset_logs: bool):
    deleted = db[ERP_SALES_COLLECTION].delete_many({}).deleted_count
    for index_name in ("row_version_1", "line_key_1", "invoice_1"):
        _drop_index_if_exists(db[ERP_SALES_COLLECTION], index_name)
    if reset_logs:
        db[ERP_SYNC_COLLECTION].delete_many({"entity": {"$in": ["sales", "sales_batch"]}})
    _ensure_erp_indexes(db)
    return deleted


def coll_storage_mb(db) -> float:
    stats = db.command("collStats", ERP_SALES_COLLECTION)
    return round((stats.get("storageSize") or 0) / 1024 / 1024, 2)


def coll_count(db) -> int:
    return db[ERP_SALES_COLLECTION].count_documents({})


def main():
    parser = argparse.ArgumentParser(description="Reconstruye erp_sales desde ChessERP en modo compacto.")
    parser.add_argument("--start", default="2022-01-01", help="Fecha desde en formato YYYY-MM-DD")
    parser.add_argument("--end", default="2026-03-31", help="Fecha hasta en formato YYYY-MM-DD")
    parser.add_argument("--chunk-days", type=int, default=7, help="Cantidad de días por tramo")
    parser.add_argument("--keep-sync-logs", action="store_true", help="Conserva erp_sync_runs previos de ventas")
    parser.add_argument("--max-storage-mb", type=float, default=470.0, help="Corta el proceso si erp_sales supera este storage en MB")
    args = parser.parse_args()

    start = parse_date(args.start)
    end = parse_date(args.end)
    if start > end:
        raise SystemExit("La fecha inicial no puede ser mayor a la final.")
    if args.chunk_days < 1:
        raise SystemExit("chunk-days debe ser >= 1")

    db = get_db()
    if db is None:
        raise SystemExit("MongoDB no está configurado.")

    print(f"[{datetime.now(timezone.utc).isoformat()}] Iniciando rebuild de ventas {start.isoformat()} a {end.isoformat()}")
    print(f"Modo compacto activo: {_sales_compact_mode()}")
    print(f"Chunk days: {args.chunk_days}")
    print(f"Max storage MB: {args.max_storage_mb}")
    print("Autenticando contra ChessERP...")
    session = erp_login()
    cookie = session.get("cookie")
    if not cookie:
        raise SystemExit("No se pudo obtener cookie de sesión de ChessERP.")

    deleted = purge_sales(db, reset_logs=not args.keep_sync_logs)
    print(f"Ventas eliminadas antes del rebuild: {deleted}")

    total_rows_read = 0
    total_rows_valid = 0
    total_stored = 0
    chunk_number = 0

    for chunk_start, chunk_end in iter_chunks(start, end, args.chunk_days):
        chunk_number += 1
        print(f"[chunk {chunk_number}] Fetch {chunk_start} -> {chunk_end}")
        dataset = fetch_sales_dataset(chunk_start, chunk_end, detailed=True, cookie=cookie)
        summary = sync_erp_sales(
            dataset.get("records") or [],
            chunk_start,
            chunk_end,
            origin="rebuild_history",
            rows_read=dataset.get("rowsRead", 0),
            warning=dataset.get("warning"),
        )
        total_rows_read += int(dataset.get("rowsRead", 0) or 0)
        total_rows_valid += int(dataset.get("rowsValid", 0) or 0)
        total_stored += int(summary.get("recordsReceived", 0) or 0)
        current_count = coll_count(db)
        current_storage = coll_storage_mb(db)
        print(
            f"[chunk {chunk_number}] rowsRead={dataset.get('rowsRead', 0)} "
            f"rowsValid={dataset.get('rowsValid', 0)} stored={summary.get('recordsReceived', 0)} "
            f"deleted={summary.get('deleted', 0)} totalDocs={current_count} storageMB={current_storage}"
        )
        if args.max_storage_mb and current_storage >= args.max_storage_mb:
            print(f"Se alcanzó el límite de storage configurado ({args.max_storage_mb} MB). Se detiene el rebuild.")
            return 2

    print("Rebuild finalizado.")
    print(
        f"Resumen: rowsRead={total_rows_read} rowsValid={total_rows_valid} "
        f"stored={total_stored} totalDocs={coll_count(db)} storageMB={coll_storage_mb(db)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Proceso interrumpido por el usuario.", file=sys.stderr)
        raise SystemExit(130)
