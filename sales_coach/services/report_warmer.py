"""Precalcula el informe base de Comercial después de cada sync.

La sync programada corre en otro proceso (scripts/sync_scheduler.py), así que el
servidor web no se entera cuando termina. Un hilo liviano mira cada pocos
minutos la última corrida terminada en Mongo (que siempre está disponible) y,
solo cuando cambia, calcula las series de Comercial de los últimos 12 meses y el
mes en curso sin filtros. Eso deja el informe en el caché con la misma clave que
pide el navegador y, de paso, despierta ClickHouse antes de que entre el primer
usuario; entre syncs no se lo consulta, así puede suspenderse por inactividad.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pymongo import DESCENDING

from sales_coach.cache import cached_status, invalidate_status_cache
from sales_coach.config import int_env

LOGGER = logging.getLogger("codenoa.perf")
BUSINESS_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")
WARM_CHECK_SECONDS = int_env("APP_WARM_CHECK_SECONDS", 300, minimum=30)
# Usuarios sin restricción de datos (admin, gerencia): resolve_data_scope da {}.
UNRESTRICTED_SCOPE: dict = {}
_NOT_CHECKED = object()


def rolling_window(period_end: str | None, today: date | None = None):
    """Misma ventana que rollingBiWindow() del navegador: 12 meses completos + el en curso."""
    today = today or datetime.now(BUSINESS_TIMEZONE).date()
    end = today
    if period_end:
        end = min(end, date.fromisoformat(str(period_end)[:10]))
    month_index = end.year * 12 + end.month - 1 - 12
    start = date(month_index // 12, month_index % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def latest_finished_sync(db):
    if db is None:
        return None
    run = db["sync_runs"].find_one(
        {"finished_at": {"$ne": None}},
        {"_id": 0, "run_id": 1, "finished_at": 1},
        sort=[("finished_at", DESCENDING)],
    )
    return (run.get("run_id"), str(run.get("finished_at"))) if run else None


def warm_default_series():
    from clickhouse_client import clickhouse_configured, get_clickhouse_storage_status
    from mongo_client import get_erp_storage_status
    from sales_coach.routes.analysis_routes import (
        _record_summary,
        cached_report,
        compute_series_report,
        series_request_key,
    )

    if not clickhouse_configured():
        return None
    # Mismo fin de datos que /api/data/freshness, que es lo que recibe el
    # navegador de un usuario comercial: ClickHouse y, si no, Mongo.
    clickhouse = cached_status("clickhouse_storage", get_clickhouse_storage_status)
    mongo = cached_status("mongo_storage", get_erp_storage_status)
    fecha_desde, fecha_hasta = rolling_window(clickhouse.get("periodEnd") or mongo.get("periodEnd"))
    request_key = series_request_key(fecha_desde, fecha_hasta)
    _, _, timings = cached_report(
        "series",
        request_key,
        UNRESTRICTED_SCOPE,
        lambda: compute_series_report(request_key),
        extra=_record_summary,
        heavy=False,
        user="warmer",
    )
    return timings


class ReportWarmer(threading.Thread):
    def __init__(self, get_db, interval_seconds: float = WARM_CHECK_SECONDS):
        super().__init__(name="report-warmer", daemon=True)
        self.get_db = get_db
        self.interval_seconds = interval_seconds
        self.last_sync = _NOT_CHECKED
        self._stop = threading.Event()

    def tick(self):
        """Precalcula al arrancar y después de cada sync nueva; si no, no hace nada."""
        marker = latest_finished_sync(self.get_db())
        if marker == self.last_sync:
            return False
        if self.last_sync is not _NOT_CHECKED:
            # Hubo una sync en otro proceso: estados e informes guardados quedaron viejos.
            invalidate_status_cache()
        warm_default_series()
        self.last_sync = marker
        return True

    def run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # el próximo intento vuelve a probar
                LOGGER.info(json.dumps({"event": "warm_failed", "error": str(exc)[:300]}, ensure_ascii=False))
            self._stop.wait(self.interval_seconds)

    def stop(self):
        self._stop.set()
