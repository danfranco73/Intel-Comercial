from __future__ import annotations

import gzip
import json
import threading
import time
from threading import BoundedSemaphore
from types import SimpleNamespace

import mongomock
import pytest

from sales_coach import concurrency
from sales_coach.cache import ResultCache, invalidate_status_cache, result_cache
from sales_coach.server import AppHandler


class FakeService:
    calls = 0
    delay = 0.0

    def __init__(self):
        self.timings = {"load_ms": 1.0}

    def analyze(self, data, scope):
        FakeService.calls += 1
        time.sleep(FakeService.delay)
        return {"meta": {"rowsAnalyzed": 3, "rowsUniverse": 5}, "summary": {"salesCurrent": 270}, "filters": data.get("filters")}


@pytest.fixture
def handler_factory(monkeypatch):
    FakeService.calls = 0
    FakeService.delay = 0.0
    db = mongomock.MongoClient().db

    def make(payload, scope=None):
        handler = AppHandler.__new__(AppHandler)
        handler.data_scope = scope
        handler.auth_context = SimpleNamespace(user=SimpleNamespace(id="u1"))
        handler._read_json_body = lambda: payload
        handler.database = lambda: db
        handler._analysis_service = FakeService
        handler.log_application_error = lambda *args, **kwargs: None
        handler.sent = []
        handler.send_precompressed_json = lambda body, status=200, headers=None: handler.sent.append(
            (status, json.loads(gzip.decompress(body)), headers or {})
        )
        handler.send_json = lambda body, status=200, headers=None: handler.sent.append((status, body, headers or {}))
        return handler

    return make


PAYLOAD = {"datasets": {"sales": {"source": "auto", "fechaDesde": "2026-06-01", "fechaHasta": "2026-06-30"}}, "filters": {}}


def test_same_report_is_computed_once_and_then_served_from_cache(handler_factory):
    first = handler_factory(PAYLOAD)
    first.handle_analyze()
    second = handler_factory(json.loads(json.dumps(PAYLOAD)))
    second.handle_analyze()

    assert FakeService.calls == 1
    assert first.sent[0][1] == second.sent[0][1]
    assert 'cache;desc="miss"' in first.sent[0][2]["Server-Timing"]
    assert 'cache;desc="hit"' in second.sent[0][2]["Server-Timing"]


def test_different_filters_or_scope_are_not_shared(handler_factory):
    handler_factory(PAYLOAD).handle_analyze()
    handler_factory({**PAYLOAD, "filters": {"caliber": ["1L"]}}).handle_analyze()
    handler_factory(PAYLOAD, scope={"seller_name": ["Vendedora Norte"]}).handle_analyze()
    assert FakeService.calls == 3


def test_concurrent_identical_requests_share_one_computation(handler_factory):
    FakeService.delay = 0.2
    handlers = [handler_factory(PAYLOAD) for _ in range(4)]
    threads = [threading.Thread(target=handler.handle_analyze) for handler in handlers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert FakeService.calls == 1
    states = sorted(handler.sent[0][2]["Server-Timing"].split(",")[0] for handler in handlers)
    assert states.count('cache;desc="miss"') == 1
    assert all(handler.sent[0][0] == 200 for handler in handlers)


def test_cache_hit_does_not_need_an_analysis_slot(handler_factory, monkeypatch):
    handler_factory(PAYLOAD).handle_analyze()
    monkeypatch.setattr(concurrency, "_analysis_slots", BoundedSemaphore(1))
    monkeypatch.setattr(concurrency, "ANALYSIS_QUEUE_TIMEOUT_SECONDS", 0.01)
    with concurrency.heavy_analysis_slot(timeout=0.01):
        cached = handler_factory(PAYLOAD)
        cached.handle_analyze()
        fresh = handler_factory({**PAYLOAD, "filters": {"brand": ["Marca A"]}})
        fresh.handle_analyze()
    assert cached.sent[0][0] == 200
    assert fresh.sent[0][0] == 503


def test_sync_invalidation_drops_cached_reports(handler_factory):
    handler_factory(PAYLOAD).handle_analyze()
    invalidate_status_cache()
    handler_factory(PAYLOAD).handle_analyze()
    assert FakeService.calls == 2
    assert result_cache.stats()["entries"] == 1


def test_result_cache_evicts_oldest_entries_over_the_byte_budget():
    cache = ResultCache(ttl_seconds=60, max_bytes=10)
    cache.put("a", b"12345")
    cache.put("b", b"12345")
    cache.put("c", b"12345")
    assert cache.get("a") is None
    assert cache.get("b") and cache.get("c")
    assert cache.stats() == {"entries": 2, "bytes": 10}
