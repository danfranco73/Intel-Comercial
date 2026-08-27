from __future__ import annotations

from datetime import date, datetime, timezone

import scripts.sync_scheduler as scheduler


def _payload(dt: datetime, monkeypatch, **env) -> dict:
    for key in (
        "SYNC_SALES_LOOKBACK_DAYS",
        "SYNC_SALES_DEEP_LOOKBACK_DAYS",
        "SYNC_SALES_WEEKLY_LOOKBACK_DAYS",
        "SYNC_SALES_DEEP_HOUR",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    return scheduler.build_payload(dt)


def _span_days(payload: dict) -> int:
    start = date.fromisoformat(payload["fechaDesde"])
    end = date.fromisoformat(payload["fechaHasta"])
    return (end - start).days + 1


def test_daytime_run_uses_short_window(monkeypatch):
    payload = _payload(datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc), monkeypatch)
    assert _span_days(payload) == 15
    assert payload["forceRefreshSales"] is True
    assert payload["fechaHasta"] == "2026-08-27"


def test_nightly_run_uses_deep_window(monkeypatch):
    # 2026-08-27 es jueves -> ventana profunda, no la semanal
    payload = _payload(datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc), monkeypatch)
    assert _span_days(payload) == 100


def test_sunday_nightly_run_uses_weekly_window(monkeypatch):
    # 2026-08-30 es domingo
    payload = _payload(datetime(2026, 8, 30, 3, 0, tzinfo=timezone.utc), monkeypatch)
    assert _span_days(payload) == 400


def test_windows_are_configurable(monkeypatch):
    payload = _payload(
        datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc),
        monkeypatch,
        SYNC_SALES_DEEP_LOOKBACK_DAYS=45,
        SYNC_SALES_DEEP_HOUR=4,
    )
    assert _span_days(payload) == 45
