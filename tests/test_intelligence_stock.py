from types import SimpleNamespace
import json
import mongomock
import pytest

from scripts.migrate_intelligence_phase1 import migrate
from sales_coach.domain.stock import normalize_stock_row
from sales_coach.domain.intelligence import cross_domain_readiness, signal
from sales_coach.repositories.deposit_repository import DepositRepository
from sales_coach.repositories.product_identity_repository import ProductIdentityRepository
from sales_coach.repositories.stock_snapshot_repository import StockSnapshotRepository
from sales_coach.services.stock_sync_service import StockSyncService
from sales_coach.services.intelligence_stock_service import IntelligenceStockService
import erp_client


def row(depot="99", **overrides):
    return {"idDeposito": int(depot), "idArticulo": 77, "idAlmacen": 0,
            "cantBultos": -2, "cantUnidades": 3, "fecha": "2026-09-01",
            "fecVtoLote": "2026-12-01", "dsArticulo": "Producto sintético", "extra_dimension": "retained", **overrides}


def configured(db, depot="99"):
    repo = DepositRepository(db)
    old = db.intelligence_deposits.find_one({"_id": depot}) or {}
    return repo.configure({"deposit_id": depot, "deposit_name": "Depósito sintético", "revision": old.get("revision"),
        "active": True, "include_in_stock_analysis": True, "relationships_verified": False}, "admin")


def test_migration_is_additive_idempotent_and_does_not_activate():
    db = mongomock.MongoClient().db
    assert migrate(db)["discovered"] == 9
    assert migrate(db)["included"] == 0
    assert db.intelligence_deposits.count_documents({}) == 9
    configured(db, "1")
    assert migrate(db)["included"] == 1
    assert db.intelligence_deposits.find_one({"_id": "1"})["deposit_name"] == "Depósito sintético"


def test_catalog_revision_and_certification_invalidate_on_change():
    db = mongomock.MongoClient().db
    item = configured(db)
    repo = DepositRepository(db)
    state = repo.configuration()
    assert repo.certify({"verified": True, "catalog_fingerprint": state["catalog_fingerprint"]}, "admin")["universe_coverage"]["status"] == "verified"
    repo.configure({"deposit_id": "99", "revision": item["revision"], "notes": "cambio"}, "admin")
    assert repo.configuration()["universe_coverage"]["status"] == "unknown"
    with pytest.raises(ValueError):
        repo.configure({"deposit_id": "99", "revision": item["revision"]}, "admin")
    before = db.intelligence_deposits.count_documents({})
    with pytest.raises(ValueError):
        repo.configure({"deposit_id": "101", "active": "true"}, "admin")
    assert db.intelligence_deposits.count_documents({}) == before


def test_stock_preserves_raw_dimensions_and_rejects_wrong_depot():
    original = row()
    normalized = normalize_stock_row(original, "99")
    assert normalized["packs"] == -2
    assert normalized["warehouse_id"] == "0"
    assert normalized["raw"] == original
    assert normalized["last_movement_date"] == "2026-09-01"
    with pytest.raises(ValueError):
        normalize_stock_row(original, "100")
    with pytest.raises(ValueError):
        normalize_stock_row(row(cantBultos=float("nan")), "99")


def test_sync_uses_all_eligible_deposits_and_failed_depot_is_not_complete():
    db = mongomock.MongoClient().db
    configured(db, "99")
    configured(db, "103")
    DepositRepository(db).discover([{"deposit_id": "120", "deposit_name": "No configurado"}])
    calls = []
    def fetch(depot, day):
        calls.append(depot)
        if depot == "103":
            raise RuntimeError("secret should never appear")
        return {"records": [row(depot), row(depot)]}
    report = StockSyncService(db, fetch).run("2026-09-29")
    assert set(calls) == {"99", "103"}
    assert report["execution_coverage"] == "incomplete"
    assert report["status"] == "partial"
    assert "secret" not in json.dumps(report)
    assert db.intelligence_stock_rows.count_documents({}) == 2
    assert all(d["started_at"] and d["finished_at"] and d["duration_ms"] >= 0 for d in report["deposits"])
    brief = IntelligenceStockService(db).brief({}, SimpleNamespace(role="admin"))
    assert brief["status"] == "partial"
    assert brief["data_quality"]["universe_coverage"]["status"] == "unknown"
    assert brief["drilldowns"]["nodes"][0]["company"] is None


def test_no_targets_and_empty_response_do_not_mean_complete_or_zero():
    db = mongomock.MongoClient().db
    def forbidden(*_):
        pytest.fail("No debe consultar Chess")
    assert StockSyncService(db, forbidden).run("2026-09-29")["status"] == "unavailable"
    configured(db)
    report = StockSyncService(db, lambda *_: {"records": []}).run("2026-09-29")
    assert report["deposits"][0]["coverage_status"] == "empty_unconfirmed"
    assert report["status"] == "unavailable"


def test_snapshot_retry_persistence_preserves_duplicate_source_rows():
    db = mongomock.MongoClient().db
    migrate(db)
    repo = StockSnapshotRepository(db)
    records = [normalize_stock_row(row(), "99")] * 2
    repo.store_rows("s", "99", records, "time")
    repo.store_rows("s", "99", records, "time")
    assert db.intelligence_stock_rows.count_documents({}) == 2


def test_product_identity_blocks_ambiguous_crosses():
    db = mongomock.MongoClient().db
    repo = ProductIdentityRepository(db)
    repo.sync([{"physical_article_id": "1", "statistical_article_id": "S"},
               {"physical_article_id": "1", "statistical_article_id": "T"}])
    assert repo.resolve("1")["status"] == "ambiguous"
    assert not cross_domain_readiness(repo.resolve("1"), units_verified=True, location_verified=True)["allowed"]
    repo.sync([{"physical_article_id": "1", "statistical_article_id": "S"},
               {"physical_article_id": "2", "statistical_article_id": "S"}])
    assert repo.physical_for_statistical("S")["status"] == "ambiguous"
    assert repo.resolve("1")["status"] == "ambiguous"
    repo.sync([{"physical_article_id": "1", "statistical_article_id": None}])
    assert repo.resolve("1")["status"] == "unknown"
    assert repo.resolve("2")["status"] == "unknown"
    assert signal("stock", {}, "quantity", {}, ["s"])["management_status"] == "detected"


def test_chess_stock_is_get_only_and_detects_payload_errors(monkeypatch):
    calls = []
    def fake(url, **kwargs):
        calls.append((url, kwargs))
        return {"dsStockFisicoApi": {"dsStock": [row()]}}, {}
    monkeypatch.setattr(erp_client, "_request_authenticated_json", fake)
    result = erp_client.fetch_stock_dataset("99", "2026-09-29")
    assert result["records"][0]["idDeposito"] == 99
    assert "/stock/?" in calls[0][0] and "idDeposito=99" in calls[0][0]
    assert calls[0][1].get("method", "GET") == "GET"
    monkeypatch.setattr(erp_client, "_request_authenticated_json", lambda *a, **k: ({"error": [{"message": "secret"}]}, {}))
    with pytest.raises(erp_client.ERPError, match="error de stock"):
        erp_client.fetch_stock_dataset("99", "2026-09-29")


def test_stock_denies_non_admin_and_filters_unknown_relationships():
    db = mongomock.MongoClient().db
    configured(db)
    StockSyncService(db, lambda *_: {"records": [row()]}).run("2026-09-29")
    service = IntelligenceStockService(db)
    with pytest.raises(PermissionError):
        service.brief({}, SimpleNamespace(role="seller"))
    assert service.items({"company": "unverified"}, SimpleNamespace(role="admin"))["rows"] == []


def test_capture_freezes_catalog_and_identity_and_quarantines_bad_rows():
    db = mongomock.MongoClient().db
    original = configured(db)
    identity = ProductIdentityRepository(db)
    version = identity.sync([{"physical_article_id": "77", "statistical_article_id": "S77"}])["version"]
    def fetch(*_):
        DepositRepository(db).configure({"deposit_id": "99", "revision": original["revision"],
            "active": False, "include_in_stock_analysis": False}, "another_admin")
        identity.sync([{"physical_article_id": "77", "statistical_article_id": "S88"}])
        return {"records": [row(), row("100")]}
    report = StockSyncService(db, fetch).run("2026-09-29")
    assert report["status"] == "partial"
    assert report["configuration"]["deposits"][0]["active"] is True
    saved = db.intelligence_stock_rows.find_one({})
    assert saved["product_identity"]["version"] == version
    assert saved["product_identity"]["statistical_article_ids"] == ["S77"]
    assert db.intelligence_stock_rejections.count_documents({}) == 1
    assert report["deposits"][0]["rows_received"] == 2
    assert report["deposits"][0]["rows_valid"] == 1


def test_failed_capture_never_replaces_complete_snapshot_and_lease_blocks_overlap():
    db = mongomock.MongoClient().db
    configured(db)
    def fetch(*_):
        with pytest.raises(ValueError, match="en ejecución"):
            StockSyncService(db, lambda *_: pytest.fail("concurrent call")).run("2026-09-29")
        return {"records": [row()]}
    first = StockSyncService(db, fetch).run("2026-09-29")
    latest = StockSyncService(db, lambda *_: {"records": []}).run("2026-09-29")
    brief = IntelligenceStockService(db).brief({}, SimpleNamespace(role="admin"))
    assert brief["summary"]["snapshot_id"] == first["snapshot_id"]
    assert brief["data_quality"]["latest_attempt"]["snapshot_id"] == latest["snapshot_id"]
    assert db.intelligence_stock_leases.count_documents({}) == 0
