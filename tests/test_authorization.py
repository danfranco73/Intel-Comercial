from __future__ import annotations

import mongomock

from security.authorization import merge_data_scope, require_permission, resolve_data_scope
from security.models import AuthenticatedUser


def user(role, **kwargs):
    return AuthenticatedUser(
        id="u1",
        email="persona@example.test",
        name="Persona",
        role=role,
        **kwargs,
    )


def test_seller_cannot_widen_scope():
    merged = merge_data_scope(
        {"seller_name": ["Vendedor Sur"]},
        {"seller_name": ["Vendedora Norte"]},
    )
    assert merged["seller_name"] == []


def test_supervisor_scope_comes_from_server_assignments():
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_many(
        [
            {"seller_key": "S1", "seller_name": "Vendedora Norte", "supervisor_key": "SUP1"},
            {"seller_key": "S2", "seller_name": "Vendedor Sur", "supervisor_key": "SUP2"},
        ]
    )
    scope = resolve_data_scope(user("supervisor", supervisor_key="SUP1"), db)
    assert scope == {"seller_name": ["Vendedora Norte"]}


def test_seller_scope_is_fail_closed_without_assignment():
    db = mongomock.MongoClient().db
    assert resolve_data_scope(user("seller"), db) == {"seller_name": []}

def test_company_scope_intersects_branches_and_limits_suppliers():
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_many(
        [
            {"seller_key": "S1", "seller_name": "Vendedora Norte", "branch_key": "B1"},
            {"seller_key": "S2", "seller_name": "Vendedor Sur", "branch_key": "B2"},
        ]
    )
    db.access_companies.insert_one(
        {
            "company_key": "EMPRESA_1",
            "name": "Empresa 1",
            "branch_keys": ["B1"],
            "suppliers": ["Proveedor A", "Proveedor B"],
            "lines": ["Vinos", "Vodka"],
            "is_active": True,
        }
    )

    scope = resolve_data_scope(
        user("commercial_director", company_key="EMPRESA_1"),
        db,
    )

    assert scope == {
        "seller_name": ["Vendedora Norte"],
        "supplier": ["Proveedor A", "Proveedor B"],
        "line": ["Vinos", "Vodka"],
    }


def test_group_economic_without_company_has_full_scope():
    db = mongomock.MongoClient().db
    assert resolve_data_scope(user("commercial_director"), db) == {}


def test_non_admin_cannot_administer():
    try:
        require_permission(user("seller", seller_key="S1"), "admin")
    except PermissionError:
        pass
    else:
        raise AssertionError("Un vendedor no debe obtener permisos administrativos")
