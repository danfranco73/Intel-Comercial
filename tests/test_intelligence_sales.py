from datetime import date, datetime
from copy import deepcopy
import mongomock
import pytest

from security.models import AuthenticatedUser
from sales_coach.services.intelligence_context_service import IntelligenceContextService
from sales_coach.services.intelligence_sales_service import IntelligenceSalesService
from sales_coach.services.sales_coverage_service import SalesCoverageService
from collections import Counter


def admin():
    return AuthenticatedUser(id="admin", email="admin@example.test", name="Admin", role="admin")


def setup(datasets):
    db = mongomock.MongoClient().db
    for key, collection in (("articles", "erp_articles"), ("sellers", "erp_sellers"), ("routes", "erp_routes")):
        db[collection].insert_many(deepcopy(datasets[key]["records"]))
    records = deepcopy(datasets["sales"]["records"])
    for r in records:
        r["company_key"] = "C1"
        r["company_name"] = "Empresa de prueba"
    lost = deepcopy(records[0])
    lost.update(date=date(2026, 5, 15), client_key="LOST", client_name="Cliente sólo anterior", amount=50, amount_net=50)
    records.append(lost)
    db.sync_runs.insert_one({"run_id": "synthetic", "entity": "sales", "status": "success",
        "range": {"fechaDesde": "2025-01-01", "fechaHasta": "2026-06-30"},
        "rows_stored": {"clickhouse": len(records)}, "finished_at": datetime(2026, 7, 1)})
    coverage = SalesCoverageService(db, daily_loader=lambda *_: dict(Counter(r["date"] for r in records)))
    context = IntelligenceContextService(db, loader=lambda *_: (records, "clickhouse", []), coverage_service=coverage)
    return db, IntelligenceSalesService(db, context)


def test_brief_reuses_engine_and_does_not_write(datasets):
    db, service = setup(datasets)
    before = {c: db[c].count_documents({}) for c in db.list_collection_names()}
    result = service.brief({"as_of": "2026-06-30"}, admin())
    assert result["summary"]["net_sales"]["value"] is not None
    assert result["summary"]["objective"] is None
    assert result["summary"]["closing_projection"]["method"] == "existing_calendar_linear"
    assert result["data_quality"]["expectation_model"]["status"] == "not_modeled"
    assert {c: db[c].count_documents({}) for c in db.list_collection_names()} == before


def test_unknown_filters_do_not_relax_and_empty_scope_denies(datasets):
    db, service = setup(datasets)
    assert service.drilldown({"as_of": "2026-06-30", "company": "does-not-exist"}, admin())["nodes"] == []
    user = AuthenticatedUser(id="s", email="s@example.test", name="S", role="supervisor")
    assert service.drilldown({"as_of": "2026-06-30"}, user)["nodes"] == []
    with pytest.raises(ValueError):
        service.brief({"as_of": "2026-06-30", "unknown": "x"}, admin())


def test_company_scope_requires_verified_mapping(datasets):
    db, service = setup(datasets)
    user = AuthenticatedUser(id="d", email="d@example.test", name="D", role="commercial_director", company_key="ACCESS")
    with pytest.raises(PermissionError):
        service.brief({"as_of": "2026-06-30"}, user)


def test_unverified_coverage_does_not_publish_false_totals(datasets):
    db, service = setup(datasets)
    db.sync_runs.delete_many({})
    report = service.brief({"as_of": "2026-06-30"}, admin())
    assert report["status"] == "unavailable"
    assert report["summary"]["net_sales"]["value"] is None
    assert report["summary"]["closing_projection"]["value"] is None


def test_drilldown_reconciles_and_detects_context_changes(datasets):
    db, service = setup(datasets)
    report = service.drilldown({"as_of": "2026-06-30", "dimension": "client"}, admin())
    assert sum(n["sales"]["current"] for n in report["nodes"]) == pytest.approx(report["totals"]["current"])
    assert any(n["sales"]["current"] == 0 and n["sales"]["previous"] > 0 for n in report["nodes"])
    with pytest.raises(ValueError, match="contexto cambió"):
        service.drilldown({"as_of": "2026-06-30", "context_id": "old"}, admin())


def test_sync_warning_or_active_writer_prevents_certifying_sales(datasets):
    db, service = setup(datasets)
    db.sync_runs.update_one({}, {"$set": {"status": "warning"}})
    assert service.brief({"as_of": "2026-06-30"}, admin())["summary"]["net_sales"]["value"] is None
    db.sync_runs.update_one({}, {"$set": {"status": "success"}})
    db.sync_runs.insert_one({"entity": "sales", "status": "running",
        "range": {"fechaDesde": "2026-06-01", "fechaHasta": "2026-06-30"}})
    assert service.brief({"as_of": "2026-06-30"}, admin())["status"] == "unavailable"


def test_mtd_coverage_does_not_authorize_full_history_insights(datasets):
    db, service = setup(datasets)
    db.sync_runs.update_one({}, {"$set": {"range.fechaDesde": "2026-06-01"}})
    records = service.context_service.loader(None, None, None)[0]
    db.sync_runs.update_one({}, {"$set": {"rows_stored.clickhouse": sum(r["date"].month == 6 and r["date"].year == 2026 for r in records)}})
    result = service.brief({"as_of": "2026-06-30"}, admin())
    assert result["summary"]["net_sales"]["value"] is not None
    assert result["summary"]["historical_benchmark"]["value"] is None
    assert result["explanations"] == []
    assert result["summary"]["comparisons"][0]["sales"]["delta"] is None


def test_objective_uses_scoped_context_and_requested_cutoff(datasets):
    db, service = setup(datasets)
    db.commercial_objectives.insert_one({"objective_id": "synthetic-goal", "version": 1,
        "company_key": "C1", "period": "2026-06", "current": True, "status": "active",
        "scope_type": "company", "scope_key": "C1", "metric": "net_sales", "target_value": 1000})
    result = service.brief({"as_of": "2026-06-30", "company": "C1"}, admin())
    objective = result["summary"]["objective"]
    assert objective["progress"]["actual_value"] == result["summary"]["net_sales"]["value"]
    assert objective["progress"]["calculated_at"] == "2026-06-30"
    assert service.brief({"as_of": "2026-06-30", "company": "C1", "channel": "unmatched"}, admin())["summary"]["objective"] is None


def test_supervisor_scope_uses_keys_even_when_names_collide(datasets):
    db, service = setup(datasets)
    first = db.erp_sellers.find_one({})
    db.erp_sellers.update_many({}, {"$set": {"seller_name": "Same name", "supervisor_key": "OTHER"}})
    db.erp_sellers.update_one({"_id": first["_id"]}, {"$set": {"supervisor_key": "SUP"}})
    user = AuthenticatedUser(id="sup", email="sup@example.test", name="Sup", role="supervisor", supervisor_key="SUP")
    report = service.drilldown({"as_of": "2026-06-30", "dimension": "seller"}, user)
    assert {n["key"] for n in report["nodes"]} <= {first["seller_key"]}
