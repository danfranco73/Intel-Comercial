#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mongo_client import get_db  # noqa: E402
from sales_coach.repositories import CoachRuleRepository  # noqa: E402


def main() -> int:
    db = get_db()
    if db is None:
        print("MongoDB no está configurado.", file=sys.stderr)
        return 1
    repository = CoachRuleRepository(db)
    rule_set = repository.seed_defaults()
    print(
        f"Reglas Sales Coach disponibles: {rule_set['rule_set_id']} "
        f"v{rule_set['version']} ({len(rule_set['rules'])} reglas)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
