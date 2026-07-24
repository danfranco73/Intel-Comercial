from __future__ import annotations

import app

from sales_coach.repositories.sales_repository import SalesRepository
from sales_coach.routes import GET_ROUTES, POST_ROUTES
from sales_coach.schemas import LoginRequest, SyncRequest, UserCreateRequest
from sales_coach.server import AppHandler, ReusableHTTPServer


def test_app_module_remains_a_compatible_facade():
    assert app.AppHandler is AppHandler
    assert app.ReusableHTTPServer is ReusableHTTPServer
    assert callable(app.main)
    assert callable(app._sync_sales_range_chunked)


def test_request_schemas_validate_at_transport_boundary():
    assert LoginRequest.parse({"email": "USER@example.test", "password": "x"}).email == "user@example.test"
    sync = SyncRequest.parse({"fechaDesde": "2026-01-01", "fechaHasta": "2026-01-31"})
    assert sync.fecha_desde == "2026-01-01"
    user = UserCreateRequest.parse(
        {
            "email": "seller@example.test",
            "name": "Seller",
            "password": "ClaveSegura123",
            "role": "seller",
            "seller_key": "S1",
        }
    )
    assert user.seller_key == "S1"


def test_sync_schema_rejects_inverted_range():
    try:
        SyncRequest.parse({"fechaDesde": "2026-02-01", "fechaHasta": "2026-01-01"})
    except ValueError as exc:
        assert "fechaDesde" in str(exc)
    else:
        raise AssertionError("El rango invertido debe rechazarse antes del servicio")


def test_route_registry_centralizes_permission_and_csrf():
    assert GET_ROUTES["/api/users"].permission == "admin"
    assert POST_ROUTES["/api/users"].permission == "admin"
    assert POST_ROUTES["/api/users"].csrf
    assert POST_ROUTES["/api/analyze"].permission == "commercial.read"


def test_sales_repository_falls_back_to_clickhouse(monkeypatch):
    repository = SalesRepository()
    monkeypatch.setattr(
        repository,
        "load_mongo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("mongo unavailable")),
    )
    monkeypatch.setattr(
        repository,
        "load_clickhouse",
        lambda *_args, **_kwargs: {"records": [{"amount": 1}]},
    )
    dataset, source = repository.load_preferred("2026-01-01", "2026-01-31")
    assert source == "clickhouse"
    assert dataset["records"][0]["amount"] == 1
