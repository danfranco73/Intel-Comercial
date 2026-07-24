from __future__ import annotations

import mongomock
import pytest

from security.models import AuthenticatedUser
from sales_coach.services.objective_service import ObjectiveService


def user(role: str, **scope):
    return AuthenticatedUser(
        id=scope.pop("id", role),
        email=f"{role}@example.test",
        name=role,
        role=role,
        seller_key=scope.pop("seller_key", None),
        supervisor_key=scope.pop("supervisor_key", None),
        branch_keys=tuple(scope.pop("branch_keys", ())),
        sales_force_keys=tuple(scope.pop("sales_force_keys", ())),
    )


def objective_payload(**overrides):
    payload = {
        "period": "2026-07",
        "scope_type": "seller",
        "scope_key": "S1",
        "metric": "net_sales",
        "target_value": "200",
        "baseline_value": "150",
        "notes": "Meta inicial",
    }
    payload.update(overrides)
    return payload


def test_objective_lifecycle_is_versioned_and_auditable():
    db = mongomock.MongoClient().db
    service = ObjectiveService(db)
    director = user("commercial_director")

    created = service.create(objective_payload(), director, {})
    updated = service.update(
        {"objective_id": created["objective_id"], "target_value": "250"},
        director,
        {},
    )
    active = service.approve(
        {"objective_id": created["objective_id"]}, director, {}
    )
    closed = service.close(
        {"objective_id": created["objective_id"]}, director, {}
    )
    history = service.history(created["objective_id"], director, {})

    assert created["version"] == 1
    assert updated["version"] == 2
    assert active["status"] == "active"
    assert closed["status"] == "closed"
    assert [item["version"] for item in history] == [2, 1]
    assert history[1]["current"] is False


def test_seller_only_sees_own_objectives():
    db = mongomock.MongoClient().db
    service = ObjectiveService(db)
    director = user("commercial_director")
    service.create(objective_payload(scope_key="S1"), director, {})
    service.create(objective_payload(scope_key="S2"), director, {})

    visible = service.list(user("seller", seller_key="S1"), {}, include_progress=False)

    assert [item["scope_key"] for item in visible] == ["S1"]


def test_supervisor_cannot_write_outside_resolved_scope():
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_many(
        [
            {"seller_key": "S1", "seller_name": "Vendedora Norte"},
            {"seller_key": "S2", "seller_name": "Vendedor Sur"},
        ]
    )
    service = ObjectiveService(db)
    supervisor = user("supervisor", supervisor_key="SUP1")

    service.create(
        objective_payload(scope_key="S1"),
        supervisor,
        {"seller_name": ["Vendedora Norte"]},
    )
    with pytest.raises(PermissionError, match="fuera de tu alcance"):
        service.create(
            objective_payload(scope_key="S2"),
            supervisor,
            {"seller_name": ["Vendedora Norte"]},
        )


def test_seller_cannot_create_or_approve_objectives():
    db = mongomock.MongoClient().db
    service = ObjectiveService(db)
    seller = user("seller", seller_key="S1")
    with pytest.raises(PermissionError):
        service.create(objective_payload(), seller, {})

    created = service.create(
        objective_payload(), user("commercial_director"), {}
    )
    with pytest.raises(PermissionError):
        service.approve({"objective_id": created["objective_id"]}, seller, {})


def test_progress_uses_governed_metric_and_evidence():
    db = mongomock.MongoClient().db
    db.erp_sales.insert_many(
        [
            {
                "date": "2026-07-02",
                "seller_key": "S1",
                "client_key": "C1",
                "product_key": "P1",
                "amount_net": 100.0,
                "quantity": 2.0,
            },
            {
                "date": "2026-07-05",
                "seller_key": "S1",
                "client_key": "C2",
                "product_key": "P2",
                "amount_net": 50.0,
                "quantity": 1.0,
            },
        ]
    )
    service = ObjectiveService(db)
    director = user("commercial_director")
    created = service.create(objective_payload(), director, {})

    listed = service.list(director, {}, period="2026-07")
    progress = next(
        item["progress"] for item in listed if item["objective_id"] == created["objective_id"]
    )

    assert progress["actual_value"] == 150.0
    assert progress["fulfillment_pct"] == 75.0
    assert progress["gap_value"] == -50.0
    assert progress["formula"] == "SUM(amount_net)"


def test_active_objective_cannot_be_edited():
    db = mongomock.MongoClient().db
    service = ObjectiveService(db)
    director = user("commercial_director")
    created = service.create(objective_payload(), director, {})
    service.approve({"objective_id": created["objective_id"]}, director, {})

    with pytest.raises(ValueError, match="borrador"):
        service.update(
            {"objective_id": created["objective_id"], "target_value": 300},
            director,
            {},
        )
