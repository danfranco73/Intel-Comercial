#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mongo_client import get_db  # noqa: E402
from sales_coach.repositories.objective_repository import ObjectiveRepository  # noqa: E402


def main() -> int:
    db = get_db()
    if db is None:
        print("MongoDB no está configurado.", file=sys.stderr)
        return 1
    repository = ObjectiveRepository(db)
    company_key = (os.getenv("DEFAULT_COMPANY_KEY") or "CODENOA").strip()
    candidates: dict[str, tuple[Decimal, str]] = {}
    sessions = db["sessions"].find(
        {"planning.budget.monthlyTotals": {"$exists": True}},
        {"planning.budget.monthlyTotals": 1, "updated_at": 1},
    ).sort("updated_at", -1)
    for session in sessions:
        monthly_totals = (
            ((session.get("planning") or {}).get("budget") or {}).get("monthlyTotals")
            or {}
        )
        for period, raw_value in monthly_totals.items():
            if period in candidates:
                continue
            try:
                value = Decimal(str(raw_value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if len(str(period)) == 7 and value >= 0:
                candidates[str(period)] = (value, str(session.get("_id")))

    created = 0
    skipped = 0
    for period, (target, session_id) in sorted(candidates.items()):
        migration_key = f"phase4:session-budget:{company_key}:{period}"
        existing = db["commercial_objectives"].find_one(
            {
                "$or": [
                    {"migration_key": migration_key},
                    {
                        "company_key": company_key,
                        "period": period,
                        "scope_type": "company",
                        "scope_key": company_key,
                        "metric": "net_sales",
                        "current": True,
                    },
                ]
            }
        )
        if existing:
            skipped += 1
            continue
        repository.create(
            {
                "company_key": company_key,
                "period": period,
                "scope_type": "company",
                "scope_key": company_key,
                "metric": "net_sales",
                "target_value": target,
                "baseline_value": None,
                "notes": f"Migrado desde planning.budget de sesión {session_id}",
            },
            "phase4_migration",
            status="draft",
            migration_key=migration_key,
        )
        created += 1

    print(
        f"Migración Fase 4 aplicada: {created} objetivos borrador creados, "
        f"{skipped} existentes omitidos."
    )
    print(
        "Los porcentajes de simulación no se migraron porque no representan importes gobernados."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
