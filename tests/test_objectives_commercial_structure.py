from __future__ import annotations

import mongomock
import pytest

from security.models import AuthenticatedUser
from sales_coach.domain.commercial_structure import resolve_commercial_structure_key
from sales_coach.schemas.objectives import ObjectiveCreateRequest
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
        "scope_type": "commercial_structure",
        "scope_key": "1",
        "metric": "net_sales",
        "target_value": "200",
        "baseline_value": "150",
        "notes": "Meta inicial",
    }
    payload.update(overrides)
    return payload


def test_resolve_commercial_structure_key_accepts_key_or_label():
    assert resolve_commercial_structure_key("1") == "1"
    assert resolve_commercial_structure_key("Bebidas") == "1"
    assert resolve_commercial_structure_key("  mercaderia ") == "2"
    assert resolve_commercial_structure_key("b2b") == "5"
    assert resolve_commercial_structure_key("no existe") is None


def test_create_request_normalizes_label_to_canonical_key():
    request = ObjectiveCreateRequest.parse(
        objective_payload(scope_key="Frescos"), "CODENOA"
    )
    assert request.scope_key == "3"


def test_create_request_rejects_unknown_commercial_structure():
    with pytest.raises(ValueError, match="esquema comercial válido"):
        ObjectiveCreateRequest.parse(objective_payload(scope_key="Congelados"), "CODENOA")


def test_progress_for_commercial_structure_scope():
    db = mongomock.MongoClient().db
    db.erp_sales.insert_many(
        [
            {
                "date": "2026-07-02",
                "seller_key": "S1",
                "client_key": "C1",
                "product_key": "P1",
                "sales_scheme_key": "1",
                "amount_net": 100.0,
                "quantity": 4.0,
            },
            {
                # Cliente app-only sin vendedor asignado: esquema B2B.
                "date": "2026-07-03",
                "seller_key": None,
                "client_key": "C9",
                "product_key": "P1",
                "sales_scheme_key": "5",
                "amount_net": 40.0,
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

    # Sólo cuenta el esquema 1 (Bebidas); el esquema 5 (B2B) queda afuera.
    assert progress["actual_value"] == 100.0


def test_progress_for_line_scope():
    db = mongomock.MongoClient().db
    db.erp_articles.insert_many(
        [
            {"product_key": "P1", "line": "Gaseosas"},
            {"product_key": "P2", "line": "Aguas"},
        ]
    )
    db.erp_sales.insert_many(
        [
            {"date": "2026-07-02", "seller_key": "S1", "client_key": "C1", "product_key": "P1", "amount_net": 100.0, "quantity": 2.0},
            {"date": "2026-07-05", "seller_key": "S1", "client_key": "C2", "product_key": "P2", "amount_net": 50.0, "quantity": 1.0},
        ]
    )
    service = ObjectiveService(db)
    director = user("commercial_director")
    created = service.create(
        objective_payload(scope_type="line", scope_key="Gaseosas"), director, {}
    )

    listed = service.list(director, {}, period="2026-07")
    progress = next(
        item["progress"] for item in listed if item["objective_id"] == created["objective_id"]
    )
    assert progress["actual_value"] == 100.0


def test_supervisor_can_write_commercial_structure_only_for_own_sales_force():
    db = mongomock.MongoClient().db
    service = ObjectiveService(db)
    supervisor = user("supervisor", sales_force_keys=["1"])

    created = service.create(objective_payload(scope_key="1"), supervisor, {})
    assert created["scope_key"] == "1"

    with pytest.raises(PermissionError, match="fuera de tu alcance"):
        service.create(objective_payload(scope_key="2"), supervisor, {})


def test_supervisor_cannot_write_line_scope():
    db = mongomock.MongoClient().db
    service = ObjectiveService(db)
    supervisor = user("supervisor", sales_force_keys=["1"])

    with pytest.raises(PermissionError):
        service.create(
            objective_payload(scope_type="line", scope_key="Gaseosas"), supervisor, {}
        )
