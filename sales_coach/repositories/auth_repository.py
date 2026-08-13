from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from security.auth import (
    authenticate,
    create_user,
    ensure_auth_indexes,
    issue_csrf_token,
    reset_user_password,
    resolve_session,
    revoke_session,
    set_user_active,
    verify_csrf,
)
from security.models import AuthContext


class AuthRepository:
    def __init__(self, db: Any):
        self.db = db

    def ensure_indexes(self) -> None:
        ensure_auth_indexes(self.db)

    def authenticate(
        self,
        email: str,
        password: str,
        ip_address: str,
        user_agent: str,
    ):
        return authenticate(self.db, email, password, ip_address, user_agent)

    def resolve_session(self, cookie_header: str | None) -> AuthContext | None:
        return resolve_session(self.db, cookie_header)

    def issue_csrf(self, context: AuthContext) -> str:
        return issue_csrf_token(self.db, context)

    def verify_csrf(self, context: AuthContext, token: str | None) -> bool:
        return verify_csrf(self.db, context, token)

    def revoke_session(self, context: AuthContext) -> None:
        revoke_session(self.db, context)

    def list_users(self) -> list[dict[str, Any]]:
        users = []
        cursor = self.db["users"].find({}, {"password_hash": 0}).sort("email", 1)
        for item in cursor:
            item["id"] = str(item.pop("_id"))
            users.append(item)
        return users

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        company_key = str(payload.get("company_key") or "").strip()
        if company_key and not self.db["access_companies"].find_one(
            {"company_key": company_key, "is_active": True}
        ):
            raise ValueError("La empresa asignada no existe o está inactiva")
        return create_user(self.db, payload)

    def set_user_active(self, user_id: str, is_active: bool) -> dict[str, Any]:
        return set_user_active(self.db, user_id, is_active)

    def reset_password(self, user_id: str, new_password: str) -> dict[str, Any]:
        return reset_user_password(self.db, user_id, new_password)

    def list_access_companies(self) -> list[dict[str, Any]]:
        return list(
            self.db["access_companies"].find(
                {}, {"_id": 0}
            ).sort("name", 1)
        )

    def save_access_company(self, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
        company_key = str(payload.get("company_key") or "").strip().upper()
        name = str(payload.get("name") or "").strip()
        branch_keys = _clean_list(payload.get("branch_keys"))
        suppliers = _clean_list(payload.get("suppliers"))
        lines = _clean_list(payload.get("lines"))
        deposit_keys = _clean_list(payload.get("deposit_keys"))
        raw_branch_names = payload.get("branch_names") if isinstance(payload.get("branch_names"), dict) else {}
        raw_deposit_names = payload.get("deposit_names") if isinstance(payload.get("deposit_names"), dict) else {}
        branch_names = {
            key: str(raw_branch_names.get(key) or key).strip()
            for key in branch_keys
        }
        deposit_names = {
            key: str(raw_deposit_names.get(key) or key).strip()
            for key in deposit_keys
        }
        if not company_key or not name:
            raise ValueError("Código y nombre de empresa son obligatorios")
        if not branch_keys:
            raise ValueError("La empresa debe incluir al menos una sucursal")
        if not suppliers:
            raise ValueError("La empresa debe incluir al menos un proveedor")
        if not lines:
            raise ValueError("La empresa debe incluir al menos una línea de producto")
        if not deposit_keys:
            raise ValueError("La empresa debe incluir al menos un depósito")
        now = datetime.now(timezone.utc)
        document = {
            "company_key": company_key,
            "name": name,
            "branch_keys": branch_keys,
            "branch_names": branch_names,
            "suppliers": suppliers,
            "lines": lines,
            "deposit_keys": deposit_keys,
            "deposit_names": deposit_names,
            "is_active": bool(payload.get("is_active", True)),
            "updated_at": now,
            "updated_by": actor_id,
        }
        self.db["access_companies"].update_one(
            {"company_key": company_key},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return document

    def access_scope_options(self) -> dict[str, list[dict[str, str]]]:
        branch_map: dict[str, str] = {}
        for item in self.db["erp_branches"].find({}, {"_id": 0}):
            key = str(item.get("branch_key") or "").strip()
            if key:
                branch_map[key] = str(item.get("branch_name") or key).strip()
        for collection_name in ("erp_sellers", "erp_routes"):
            for item in self.db[collection_name].find(
                {"branch_key": {"$nin": [None, ""]}},
                {"_id": 0, "branch_key": 1, "branch_name": 1},
            ):
                key = str(item.get("branch_key") or "").strip()
                if key:
                    branch_map[key] = str(item.get("branch_name") or key).strip()
        deposits: dict[str, str] = {
            str(item.get("deposit_key")): str(item.get("deposit_name") or item.get("deposit_key"))
            for item in self.db["erp_deposits"].find({}, {"_id": 0})
            if str(item.get("deposit_key") or "").strip()
        }
        for company in self.db["access_companies"].find(
            {}, {"_id": 0, "branch_keys": 1, "branch_names": 1, "deposit_keys": 1, "deposit_names": 1}
        ):
            for key in company.get("branch_keys") or []:
                label = (company.get("branch_names") or {}).get(str(key)) or key
                branch_map.setdefault(str(key), str(label))
            for key in company.get("deposit_keys") or []:
                label = (company.get("deposit_names") or {}).get(str(key)) or key
                deposits.setdefault(str(key), str(label))
        articles = self.db["erp_articles"].find(
            {"$or": [
                {"supplier": {"$nin": [None, "", "Sin proveedor"]}},
                {"line": {"$nin": [None, "", "Sin línea"]}},
                {"business_unit": {"$nin": [None, "", "Sin unidad de negocio"]}},
            ]},
            {"_id": 0, "supplier": 1, "line": 1, "business_unit": 1},
        )
        article_rows = list(articles)
        suppliers = sorted({
            str(item.get("supplier") or "").strip()
            for item in article_rows
            if str(item.get("supplier") or "").strip() not in {"", "Sin proveedor"}
        })
        lines = sorted({
            str(item.get("line") or "").strip()
            for item in article_rows
            if str(item.get("line") or "").strip() not in {"", "Sin línea"}
        })
        business_units = sorted({
            str(item.get("business_unit") or "").strip()
            for item in article_rows
            if str(item.get("business_unit") or "").strip() not in {"", "Sin unidad de negocio"}
        })
        sales_force_map: dict[str, str] = {}
        seller_map: dict[str, str] = {}
        for item in self.db["erp_sellers"].find(
            {}, {"_id": 0, "sales_force_key": 1, "sales_force": 1, "seller_key": 1, "seller_name": 1}
        ):
            force_key = str(item.get("sales_force_key") or "").strip()
            if force_key:
                sales_force_map.setdefault(force_key, str(item.get("sales_force") or force_key).strip())
            seller_key = str(item.get("seller_key") or "").strip()
            if seller_key:
                seller_map[seller_key] = str(item.get("seller_name") or seller_key).strip()
        return {
            "branches": [
                {"key": key, "name": name}
                for key, name in sorted(branch_map.items(), key=lambda item: item[1])
            ],
            "suppliers": [{"key": value, "name": value} for value in suppliers],
            "lines": [{"key": value, "name": value} for value in lines],
            "deposits": [
                {"key": key, "name": name}
                for key, name in sorted(deposits.items(), key=lambda item: item[1])
            ],
            "businessUnits": [{"key": value, "name": value} for value in business_units],
            "salesForces": [
                {"key": key, "name": name}
                for key, name in sorted(sales_force_map.items(), key=lambda item: item[1])
            ],
            "sellers": [
                {"key": key, "name": name}
                for key, name in sorted(seller_map.items(), key=lambda item: item[1])
            ],
        }


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
