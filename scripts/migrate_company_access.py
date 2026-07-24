"""Prepara empresas configurables y compatibilidad de usuarios existentes."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mongo_client import get_db  # noqa: E402


def main() -> int:
    db = get_db()
    db["access_companies"].create_index(
        [("company_key", 1)],
        unique=True,
        name="access_companies_key_unique",
    )
    db["erp_branches"].create_index(
        [("branch_key", 1)],
        unique=True,
        name="erp_branches_key_unique",
    )
    db["erp_deposits"].create_index(
        [("deposit_key", 1)],
        unique=True,
        name="erp_deposits_key_unique",
    )
    db["access_companies"].create_index(
        [("is_active", 1), ("name", 1)],
        name="access_companies_active_name",
    )
    result = db["users"].update_many(
        {"company_key": {"$exists": False}},
        {"$set": {"company_key": None}},
    )
    available_lines = sorted(
        value
        for value in db["erp_articles"].distinct("line")
        if str(value or "").strip() not in {"", "Sin línea"}
    )
    companies = db["access_companies"].update_many(
        {"lines": {"$exists": False}},
        {"$set": {"lines": available_lines}},
    )
    for company in db["access_companies"].find(
        {"branch_names": {"$exists": False}},
        {"branch_keys": 1},
    ):
        names = {
            str(key): str(key)
            for key in company.get("branch_keys") or []
        }
        db["access_companies"].update_one(
            {"_id": company["_id"]},
            {"$set": {"branch_names": names}},
        )
    db["access_companies"].update_many(
        {"deposit_keys": {"$exists": False}},
        {"$set": {"deposit_keys": [], "deposit_names": {}}},
    )
    print(
        "Configuración de empresas preparada. "
        f"Usuarios preservados/actualizados: {result.modified_count}. "
        f"Empresas compatibles con líneas: {companies.modified_count}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
