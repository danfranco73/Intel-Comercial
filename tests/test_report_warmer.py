from __future__ import annotations

from datetime import date, datetime, timezone

import mongomock

import sales_coach.services.report_warmer as warmer
from sales_coach.cache import result_cache


def test_rolling_window_matches_the_browser_default():
    assert warmer.rolling_window("2026-10-29", today=date(2026, 10, 29)) == ("2025-10-01", "2026-10-29")
    # Base atrasada: se corta en el último día con datos.
    assert warmer.rolling_window("2026-06-30", today=date(2026, 10, 29)) == ("2025-06-01", "2026-06-30")
    assert warmer.rolling_window(None, today=date(2026, 1, 15)) == ("2025-01-01", "2026-01-15")


def test_warmer_runs_at_start_and_after_each_new_sync_only(monkeypatch):
    db = mongomock.MongoClient().db
    warms = []
    invalidations = []
    monkeypatch.setattr(warmer, "warm_default_series", lambda: warms.append(1))
    monkeypatch.setattr(warmer, "invalidate_status_cache", lambda: invalidations.append(1))
    job = warmer.ReportWarmer(lambda: db, interval_seconds=1)

    assert job.tick() is True  # arranque, aunque todavía no haya syncs
    assert job.tick() is False  # nada nuevo: no consulta ClickHouse
    db["sync_runs"].insert_one({"run_id": "r1", "finished_at": datetime(2026, 10, 29, 3, tzinfo=timezone.utc)})
    db["sync_runs"].insert_one({"run_id": "r0", "finished_at": None})
    assert job.tick() is True
    assert job.tick() is False
    assert warms == [1, 1]
    assert invalidations == [1]


def test_warm_default_series_fills_the_cache_with_the_browser_key(monkeypatch):
    import sales_coach.routes.analysis_routes as routes
    import clickhouse_client
    import mongo_client

    calls = []
    monkeypatch.setattr(clickhouse_client, "clickhouse_configured", lambda: True)
    monkeypatch.setattr(clickhouse_client, "get_clickhouse_storage_status", lambda: {"periodEnd": "2026-06-30", "lastSyncAt": "s1"})
    monkeypatch.setattr(mongo_client, "get_erp_storage_status", lambda: {"periodEnd": "2026-06-30", "articlesLastSyncAt": "v1"})
    monkeypatch.setattr(routes, "get_clickhouse_storage_status", lambda: {"periodEnd": "2026-06-30", "lastSyncAt": "s1"})
    monkeypatch.setattr(routes, "get_erp_storage_status", lambda: {"periodEnd": "2026-06-30", "articlesLastSyncAt": "v1"})
    monkeypatch.setattr(
        routes,
        "compute_series_report",
        lambda key: calls.append(key) or ({"meta": {"rowsAnalyzed": 1}, "summary": {}}, {}),
    )

    timings = warmer.warm_default_series()
    assert timings["cache"] == "miss"
    assert calls == [{"fechaDesde": "2025-06-01", "fechaHasta": "2026-06-30", "filters": {}, "compare": False}]

    # El navegador pide lo mismo (sin filtros ni foco): sale del caché.
    _, _, browser = routes.cached_report(
        "series", routes.series_request_key("2025-06-01", "2026-06-30", {}, ""), {}, lambda: calls.append("again")
    )
    assert browser["cache"] == "hit"
    assert result_cache.stats()["entries"] == 1
