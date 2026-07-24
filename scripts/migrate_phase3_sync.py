#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mongo_client import get_db  # noqa: E402
from sales_coach.repositories.sync_repository import SyncRepository  # noqa: E402


def main() -> int:
    db = get_db()
    if db is None:
        print("MongoDB no está configurado.", file=sys.stderr)
        return 1
    SyncRepository(db).ensure_indexes()
    print("Migración Fase 3 aplicada: índices de sync_runs, sync_checkpoints y scheduler_leases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
