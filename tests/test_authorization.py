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


def test_explicit_seller_keys_restrict_any_role_without_company_or_supervisor():
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_many(
        [
            {"seller_key": "S1", "seller_name": "Vendedora Norte"},
            {"seller_key": "S2", "seller_name": "Vendedor Sur"},
            {"seller_key": "S3", "seller_name": "Vendedor Este"},
        ]
    )
    scope = resolve_data_scope(user("viewer", seller_keys=("S1", "S3")), db)
    assert scope == {"seller_name": ["Vendedor Este", "Vendedora Norte"]}


def test_explicit_seller_keys_intersect_with_company_scope():
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_many(
        [
            {"seller_key": "S1", "seller_name": "Vendedora Norte", "branch_key": "B1"},
            {"seller_key": "S2", "seller_name": "Vendedor Sur", "branch_key": "B1"},
        ]
    )
    db.access_companies.insert_one(
        {
            "company_key": "EMPRESA_1",
            "name": "Empresa 1",
            "branch_keys": ["B1"],
            "suppliers": ["Proveedor A"],
            "lines": ["Vinos"],
            "is_active": True,
        }
    )
    # Aunque la empresa habilita a los dos vendedores de la sucursal B1, la
    # restricción explícita a un socio puntual no puede ampliarse: solo debe
    # ver a Vendedora Norte, no a todo el resto de la sucursal.
    scope = resolve_data_scope(
        user("commercial_director", company_key="EMPRESA_1", seller_keys=("S1",)),
        db,
    )
    assert scope["seller_name"] == ["Vendedora Norte"]


def test_business_unit_restriction_is_independent_of_seller_scope():
    db = mongomock.MongoClient().db
    scope = resolve_data_scope(
        user("viewer", business_units=("MERCADERIAS", "BEBIDAS")),
        db,
    )
    assert scope == {"business_unit": ["BEBIDAS", "MERCADERIAS"]}


def test_sales_force_restriction_resolves_keys_to_names():
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_many(
        [
            {"seller_key": "S1", "seller_name": "Vendedora Norte", "sales_force_key": "2", "sales_force": "MERCADERIA"},
            {"seller_key": "S2", "seller_name": "Vendedor Sur", "sales_force_key": "3", "sales_force": "FRESCOS"},
        ]
    )
    scope = resolve_data_scope(user("viewer", sales_force_keys=("2",)), db)
    assert scope["sales_force"] == ["MERCADERIA"]


def test_non_admin_cannot_administer():
    try:
        require_permission(user("seller", seller_key="S1"), "admin")
    except PermissionError:
        pass
    else:
        raise AssertionError("Un vendedor no debe obtener permisos administrativos")
