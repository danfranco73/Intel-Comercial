from datetime import date, datetime, timedelta, timezone
import mongomock

from sales_coach.services.sales_coverage_service import SalesCoverageService
from sales_coach.domain.stock_sales import StockSalesCompatibility


def assess(db, counts, source="clickhouse", end="2026-09-03"):
    return SalesCoverageService(db, lambda *_: counts).assess(source, {
        "test": (date(2026, 9, 1), date.fromisoformat(end))})["test"]


def native_batch(db, count=10, timestamp=datetime(2026, 9, 4, 12), **chunk):
    return db.erp_sync_runs.insert_one({"entity": "sales_batch", "timestamp": timestamp,
        "range": {"fechaDesde": "2026-09-01", "fechaHasta": "2026-09-03"},
        "mongoStored": 0, "clickhouseStored": count, "chunks": [{
            "range": {"fechaDesde": "2026-09-01", "fechaHasta": "2026-09-03"},
            "storageMode": "clickhouse_only", "clickhouseStored": count, **chunk}]})


def warning(db, mongo=0):
    db.sync_runs.insert_one({"entity": "sales", "run_id": "retention-warning", "status": "warning",
        "range": {"fechaDesde": "2026-09-01", "fechaHasta": "2026-09-03"},
        "started_at": datetime(2026, 9, 4, 11), "finished_at": datetime(2026, 9, 4, 13),
        "rows_stored": {"mongo": mongo, "clickhouse": 10}, "reconciliation": {"consistent": False}})


def test_observed_dates_alone_do_not_certify_and_database_remains_unchanged():
    db = mongomock.MongoClient().db
    result = assess(db, {date(2026, 9, 1): 10})
    assert result["status"] == "unverified"
    assert result["unverified_ranges"] == [{"start": "2026-09-01", "end": "2026-09-03"}]
    assert db.list_collection_names() == []


def test_completed_native_chunk_certifies_zero_dates_inside_its_query_range():
    db = mongomock.MongoClient().db
    native_batch(db)
    result = assess(db, {date(2026, 9, 1): 10})
    assert result["status"] == "complete"
    assert result["confirmed_no_rows_ranges"] == [{"start": "2026-09-02", "end": "2026-09-03"}]


def test_expected_retention_warning_is_not_a_clickhouse_ingestion_failure():
    db = mongomock.MongoClient().db
    native_batch(db)
    warning(db)
    assert assess(db, {date(2026, 9, 1): 10})["status"] == "complete"
    assert db.sync_runs.find_one({})["status"] == "warning"
    assert assess(db, {}, "mongo")["status"] == "unverified"


def test_unexplained_warning_and_changed_physical_counts_remain_unverified():
    db = mongomock.MongoClient().db
    native_batch(db)
    warning(db, mongo=10)
    assert assess(db, {date(2026, 9, 1): 10})["status"] == "unverified"
    db.sync_runs.delete_many({})
    assert assess(db, {date(2026, 9, 1): 9})["status"] == "unverified"


def test_newer_complete_chunks_supersede_only_their_own_days_of_an_orphan_run():
    db = mongomock.MongoClient().db
    native_batch(db)
    db.sync_runs.insert_one({"entity": "sales", "status": "running",
        "range": {"fechaDesde": "2026-09-01", "fechaHasta": "2026-09-04"},
        "started_at": datetime(2026, 8, 1), "updated_at": datetime(2026, 8, 1)})
    result = assess(db, {date(2026, 9, 1): 10}, end="2026-09-04")
    assert result["unverified_ranges"] == [{"start": "2026-09-04", "end": "2026-09-04"}]
    db.scheduler_leases.insert_one({"_id": "sales_scheduler", "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})
    assert assess(db, {date(2026, 9, 1): 10})["certified_days"] == 0
    db.scheduler_leases.delete_many({})
    db.sync_runs.update_one({}, {"$set": {"updated_at": datetime(2026, 9, 4, 14)}})
    assert assess(db, {date(2026, 9, 1): 10})["certified_days"] == 0


def test_empty_response_warning_is_not_a_confirmed_zero_or_an_implicit_holiday():
    db = mongomock.MongoClient().db
    native_batch(db, count=0, warning="ChessERP no devolvió ventas para el rango seleccionado.")
    assert assess(db, {})["status"] == "unverified"
    db.erp_sync_runs.update_one({}, {"$set": {"chunks.0.emptyConfirmed": True}})
    assert assess(db, {})["status"] == "complete"


def test_native_mongo_range_is_not_extended_to_batch_request_before_retention():
    db = mongomock.MongoClient().db
    native_batch(db)
    db.erp_sync_runs.insert_one({"entity": "sales", "status": "success", "recordsStored": 10,
        "range": {"fechaDesde": "2026-09-03", "fechaHasta": "2026-09-03"},
        "timestamp": datetime(2026, 9, 4, 12)})
    result = assess(db, {date(2026, 9, 3): 10}, "mongo")
    assert result["unverified_ranges"] == [{"start": "2026-09-01", "end": "2026-09-02"}]


def test_product_preparation_blocks_units_scope_and_capture_date_even_with_good_identity():
    context = StockSalesCompatibility(snapshot_id="s", identity_version="v", physical_article_id="1",
        statistical_article_id="S1", identity_status="verified", sales_coverage_status="complete",
        stock_execution_coverage="complete")
    report = context.readiness()
    assert not report["join_ready"]
    assert report["blocking_reasons"] == ["STOCK_SALES_SCOPE_UNVERIFIED", "QUANTITY_EQUIVALENCE_UNVERIFIED", "TEMPORAL_ALIGNMENT_UNVERIFIED"]
    assert report["velocity"] is None and report["days_of_supply"] is None
