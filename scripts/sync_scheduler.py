#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import _erp_masters_available, _sync_sales_range_chunked  # noqa: E402
from mongo_client import get_db  # noqa: E402
from sales_coach.repositories.sync_repository import SyncRepository  # noqa: E402
from sales_coach.services.sync_service import SyncService  # noqa: E402


def int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(value, minimum)


def build_payload(now: datetime | None = None) -> dict:
    current = (now or datetime.now(timezone.utc)).date()
    lookback_days = int_env("SYNC_SALES_LOOKBACK_DAYS", 7)
    start = current - timedelta(days=lookback_days - 1)
    return {
        "fechaDesde": start.isoformat(),
        "fechaHasta": current.isoformat(),
        "forceRefreshSales": True,
        "refreshMasters": True,
    }


def run_once() -> dict:
    db = get_db()
    repository = SyncRepository(db)
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    lease_seconds = int_env("SYNC_SCHEDULER_LEASE_SECONDS", 3600, minimum=60)
    if not repository.acquire_lease("sales_scheduler", owner, lease_seconds):
        raise RuntimeError("Otra instancia del scheduler posee el lease activo")
    retries = int_env("SYNC_MAX_RETRIES", 3)
    retry_delay = int_env("SYNC_RETRY_DELAY_SECONDS", 30)
    try:
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                return SyncService(
                    _sync_sales_range_chunked,
                    _erp_masters_available,
                ).run(build_payload(), requested_by="scheduler", origin="scheduler")
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(retry_delay * attempt)
        raise RuntimeError(f"Sync falló tras {retries} intentos: {last_error}") from last_error
    finally:
        repository.release_lease("sales_scheduler", owner)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Job desacoplado para sincronizar Chess con MongoDB y ClickHouse."
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Ejecuta continuamente; sin esta opción realiza una corrida y termina.",
    )
    args = parser.parse_args()
    interval = int_env("SYNC_INTERVAL_SECONDS", 6 * 60 * 60, minimum=60)
    while True:
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = run_once()
            print(
                f"[{started}] sync={result.get('runId')} "
                f"rows={result.get('rowsValid', 0)} status=success",
                flush=True,
            )
        except Exception as exc:
            print(f"[{started}] status=failed error={exc}", file=sys.stderr, flush=True)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
