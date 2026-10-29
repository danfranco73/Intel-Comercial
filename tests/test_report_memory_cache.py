import gzip
import json
import runpy
from threading import BoundedSemaphore, Event, Thread

import pytest

from sales_coach import concurrency
from sales_coach.routes import analysis_routes


@pytest.mark.parametrize("configured, expected", [(None, 1), ("2", 2), ("0", 1), ("invalid", 1)])
def test_capacity_configuration(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("APP_MAX_CONCURRENT_ANALYSES", raising=False)
    else:
        monkeypatch.setenv("APP_MAX_CONCURRENT_ANALYSES", configured)
    assert runpy.run_path(concurrency.__file__)["MAX_CONCURRENT_ANALYSES"] == expected


@pytest.fixture
def isolated_cache(monkeypatch):
    monkeypatch.setattr(concurrency, "_analysis_slots", BoundedSemaphore(1))
    monkeypatch.setattr(concurrency, "ANALYSIS_QUEUE_TIMEOUT_SECONDS", 0.01)
    # No storage-status probes, even to mock/unconfigured connectors.
    monkeypatch.setattr(analysis_routes, "_current_data_version", lambda: "synthetic")


def test_both_report_cache_misses_share_capacity_and_hits_bypass_it(isolated_cache):
    calls = []

    def compute():
        calls.append(1)
        return {"summary": {"salesCurrent": 270, "quantity": 30}}, {}

    warm = analysis_routes.cached_report("analyze", {"key": "warm"}, {}, compute)
    entered, release = Event(), Event()

    def hold_slot():
        with concurrency.heavy_analysis_slot():
            entered.set()
            release.wait(2)

    worker = Thread(target=hold_slot)
    worker.start()
    try:
        assert entered.wait(1)
        for kind in ("analyze", "tactical_month"):
            with pytest.raises(concurrency.AnalysisCapacityExceeded):
                analysis_routes.cached_report(kind, {"key": "cold"}, {}, compute)
        hit = analysis_routes.cached_report("analyze", {"key": "warm"}, {}, compute)
        assert hit[0] == warm[0]
        assert hit[2]["cache"] == "hit"
        assert len(calls) == 1
    finally:
        release.set()
        worker.join(2)
    for kind in ("analyze", "tactical_month"):
        body, _, timing = analysis_routes.cached_report(kind, {"key": "cold"}, {}, compute)
        assert json.loads(gzip.decompress(body))["summary"]["quantity"] == 30
        assert timing["cache"] == "miss"
    assert len(calls) == 3


def test_compute_failure_releases_slot_and_allows_retry(isolated_cache):
    def fail():
        raise ValueError("synthetic failure")

    with pytest.raises(ValueError, match="synthetic failure"):
        analysis_routes.cached_report("analyze", {}, {}, fail)
    body, _, _ = analysis_routes.cached_report("analyze", {}, {}, lambda: ({"ok": True}, {}))
    assert json.loads(gzip.decompress(body)) == {"ok": True}
