from __future__ import annotations

from datetime import datetime, timedelta

import mongomock
import pytest

from sales_coach.repositories import AlertRepository
from sales_coach.services.alert_service import AlertService
from security.models import AuthenticatedUser


def user(role="admin", user_id="admin", seller_key=None):
    return AuthenticatedUser(
        id=user_id,
        email=f"{role}@example.test",
        name=role,
        role=role,
        seller_key=seller_key,
    )


def signal(**overrides):
    item = {
        "dedupe_key": "stable-key",
        "company_key": "CODENOA",
        "type": "seller_decline",
        "severity": "warning",
        "severity_rank": 2,
        "period": "2026-07",
        "seller_key": "S1",
        "seller_name": "Ana",
        "client_key": None,
        "client_name": None,
        "category_key": None,
        "metric": "sales_growth_pct",
        "evidence": {"growth_pct": -20},
        "message": "Ana cae 20%.",
        "source": "commercial_rules",
        "rule_id": "SELLER_DECLINE",
        "rule_version": 1,
        "entity_key": None,
        "assignee": {"type": "user", "user_id": "seller-1", "role": "seller", "name": "Ana"},
    }
    item.update(overrides)
    return item


def test_upsert_is_idempotent_and_preserves_human_management():
    db = mongomock.MongoClient().db
    repository = AlertRepository(db)
    first, created = repository.upsert_signal(signal(), "system")
    managed = repository.transition(
        first["alert_id"], {"assigned"}, "in_progress", "seller-1", "Contactado", None
    )
    refreshed, created_again = repository.upsert_signal(
        signal(message="Ana cae 25%.", evidence={"growth_pct": -25}), "system"
    )

    assert created is True
    assert created_again is False
    assert refreshed["alert_id"] == first["alert_id"]
    assert refreshed["status"] == "in_progress"
    assert refreshed["message"] == "Ana cae 25%."
    assert refreshed["assignee"]["user_id"] == "seller-1"
    assert len(refreshed["history"]) == len(managed["history"]) == 2


def test_resolution_requires_valid_transition_and_records_history():
    db = mongomock.MongoClient().db
    repository = AlertRepository(db)
    alert, _ = repository.upsert_signal(signal(), "system")

    resolved = repository.transition(
        alert["alert_id"], {"assigned"}, "resolved", "seller-1", None, "Cliente recuperado"
    )

    assert resolved["status"] == "resolved"
    assert resolved["result"] == "Cliente recuperado"
    assert resolved["history"][-1]["from_status"] == "assigned"
    assert resolved["history"][-1]["to_status"] == "resolved"
    with pytest.raises(ValueError):
        repository.transition(
            alert["alert_id"], {"assigned"}, "in_progress", "seller-1", None, None
        )


def test_seller_and_supervisor_visibility_isolated_by_backend_scope():
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_many(
        [
            {"seller_key": "S1", "seller_name": "Ana"},
            {"seller_key": "S2", "seller_name": "Bruno"},
        ]
    )
    repository = AlertRepository(db)
    repository.upsert_signal(signal(), "system")
    repository.upsert_signal(signal(dedupe_key="other", seller_key="S2", seller_name="Bruno"), "system")
    service = AlertService(db)

    seller_rows = service.list(user("seller", "seller-1", "S1"), {"seller_name": ["Ana"]})
    supervisor_rows = service.list(user("supervisor"), {"seller_name": ["Bruno"]})

    assert {item["seller_key"] for item in seller_rows} == {"S1"}
    assert {item["seller_key"] for item in supervisor_rows} == {"S2"}
    with pytest.raises(PermissionError):
        service.history(supervisor_rows[0]["alert_id"], user("seller", "seller-1", "S1"), {"seller_name": ["Ana"]})


def test_seller_can_resolve_own_alert_but_cannot_dismiss_it():
    db = mongomock.MongoClient().db
    repository = AlertRepository(db)
    alert, _ = repository.upsert_signal(signal(), "system")
    service = AlertService(db)
    seller = user("seller", "seller-1", "S1")

    managed = service.transition(
        {"alert_id": alert["alert_id"], "status": "in_progress", "comment": "Visita agendada"},
        seller,
        {"seller_name": ["Ana"]},
    )

    assert managed["status"] == "in_progress"
    with pytest.raises(PermissionError):
        service.transition(
            {"alert_id": alert["alert_id"], "status": "dismissed", "comment": "No aplica"},
            seller,
            {"seller_name": ["Ana"]},
        )


def test_operational_signals_cover_sync_delay_and_storage_divergence(monkeypatch):
    db = mongomock.MongoClient().db
    db.sync_runs.insert_one(
        {
            "run_id": "run-1",
            "entity": "sales",
            "status": "warning",
            "finished_at": datetime.now().replace(tzinfo=None) - timedelta(hours=40),
            "reconciliation": {"consistent": False, "difference": 12},
        }
    )
    monkeypatch.setenv("ALERT_SYNC_MAX_AGE_HOURS", "30")

    summary = AlertService(db).generate_operational()
    rows = list(db.commercial_alerts.find({}, {"_id": 0, "type": 1, "evidence": 1}))

    assert summary["created"] == 2
    assert {item["type"] for item in rows} == {"sync_delayed", "storage_divergence"}
    assert all(item["evidence"] for item in rows)


def test_overdue_alerts_are_persisted_on_read():
    db = mongomock.MongoClient().db
    repository = AlertRepository(db)
    alert, _ = repository.upsert_signal(signal(), "system")
    repository.collection.update_one(
        {"alert_id": alert["alert_id"]},
        {"$set": {"due_date": "2020-01-01", "status": "assigned"}},
    )

    rows = repository.list({"company_key": "CODENOA"})

    assert rows[0]["status"] == "overdue"
    assert repository.get(alert["alert_id"])["status"] == "overdue"
