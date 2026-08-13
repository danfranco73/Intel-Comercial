from __future__ import annotations

from typing import Any

from security.models import AuthenticatedUser


ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"commercial.read", "commercial.write", "admin", "sync", "users"}),
    "commercial_director": frozenset({"commercial.read", "commercial.write"}),
    "supervisor": frozenset({"commercial.read", "commercial.write"}),
    "seller": frozenset({"commercial.read"}),
    "viewer": frozenset({"commercial.read"}),
}


def has_permission(user: AuthenticatedUser, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.role, frozenset())


def require_permission(user: AuthenticatedUser, permission: str) -> None:
    if not has_permission(user, permission):
        raise PermissionError("No tenés permisos para realizar esta operación")


def require_role(user: AuthenticatedUser, *roles: str) -> None:
    if user.role not in roles:
        raise PermissionError("Tu rol no tiene acceso a esta operación")


def resolve_data_scope(user: AuthenticatedUser, db: Any) -> dict[str, list[str]]:
    """Resolve an immutable backend scope using persisted ERP masters."""
    if user.role == "admin":
        return {}

    clauses: list[dict[str, Any]] = []
    if user.role == "seller":
        if not user.seller_key:
            return {"seller_name": []}
        clauses.append({"seller_key": user.seller_key})
    elif user.role == "supervisor":
        if user.supervisor_key:
            clauses.append({"supervisor_key": user.supervisor_key})
        if user.sales_force_keys:
            clauses.append({"sales_force_key": {"$in": list(user.sales_force_keys)}})
        if user.branch_keys:
            clauses.append({"branch_key": {"$in": list(user.branch_keys)}})
        if not clauses:
            return {"seller_name": []}

    role_query = (
        clauses[0]
        if len(clauses) == 1
        else {"$or": clauses}
        if clauses
        else None
    )
    query_parts = [role_query] if role_query else []
    company = None
    if user.company_key:
        company = db["access_companies"].find_one(
            {"company_key": user.company_key, "is_active": True},
            {"_id": 0, "branch_keys": 1, "suppliers": 1, "lines": 1},
        )
        if company is None:
            return {"seller_name": [], "supplier": []}
        branches = list(company.get("branch_keys") or [])
        if not branches:
            return {"seller_name": [], "supplier": []}
        query_parts.append({"branch_key": {"$in": branches}})

    # Restricción explícita a un listado puntual de vendedores, independiente
    # del rol y combinable con cualquiera de los anteriores (se intersecta,
    # nunca amplía lo que el rol/empresa ya permiten).
    if user.seller_keys:
        query_parts.append({"seller_key": {"$in": list(user.seller_keys)}})

    scope: dict[str, list[str]] = {}
    if query_parts:
        # Todos los límites configurados (rol, empresa, vendedores puntuales)
        # se intersectan en backend.
        query = query_parts[0] if len(query_parts) == 1 else {"$and": query_parts}
        sellers = list(
            db["erp_sellers"].find(query, {"_id": 0, "seller_name": 1})
        )
        scope["seller_name"] = sorted(
            {
                str(item.get("seller_name") or "").strip()
                for item in sellers
                if str(item.get("seller_name") or "").strip()
            }
        )
    if company is not None:
        scope["supplier"] = sorted(
            {
                str(value).strip()
                for value in company.get("suppliers") or []
                if str(value).strip()
            }
        )
        scope["line"] = sorted(
            {
                str(value).strip()
                for value in company.get("lines") or []
                if str(value).strip()
            }
        )

    # Restricción directa por unidad de negocio, a nivel de línea de venta
    # (no depende de a qué vendedor está atribuida la venta).
    if user.business_units:
        scope["business_unit"] = sorted(
            {str(value).strip() for value in user.business_units if str(value).strip()}
        )

    # Restricción directa por fuerza de venta, a nivel de línea de venta.
    # Reutiliza sales_force_keys (ya usado arriba para acotar supervisores
    # por fuerza de venta vía erp_sellers) resolviendo a los nombres visibles
    # en las ventas, para que cualquier rol pueda quedar limitado a una o más
    # fuerzas de venta sin depender de la jerarquía de supervisión.
    if user.sales_force_keys:
        forces = list(
            db["erp_sellers"].find(
                {"sales_force_key": {"$in": list(user.sales_force_keys)}},
                {"_id": 0, "sales_force": 1},
            )
        )
        sales_force_names = sorted(
            {
                str(item.get("sales_force") or "").strip()
                for item in forces
                if str(item.get("sales_force") or "").strip()
            }
        )
        if sales_force_names:
            scope["sales_force"] = sales_force_names

    return scope


def merge_data_scope(
    requested_filters: dict[str, Any] | None,
    scope_filters: dict[str, list[str]] | None,
) -> dict[str, Any]:
    """Apply server scope as an intersection; it can never be widened by the client."""
    merged = dict(requested_filters or {})
    for field, allowed in (scope_filters or {}).items():
        requested = merged.get(field)
        if requested is None:
            merged[field] = list(allowed)
            continue
        requested_values = {
            str(value)
            for value in (requested if isinstance(requested, list) else [requested])
        }
        merged[field] = [value for value in allowed if str(value) in requested_values]
    return merged


def restrict_filter_options(
    filters: dict[str, Any],
    scope_filters: dict[str, list[str]] | None,
) -> dict[str, Any]:
    restricted = dict(filters or {})
    for field, allowed in (scope_filters or {}).items():
        config = restricted.get(field)
        if not config:
            continue
        allowed_set = {str(value) for value in allowed}
        restricted[field] = {
            **config,
            "options": [
                option
                for option in config.get("options", [])
                if str(option.get("value")) in allowed_set
            ],
        }
    return restricted
