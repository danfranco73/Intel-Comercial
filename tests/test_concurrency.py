from __future__ import annotations

from threading import BoundedSemaphore

import pytest

from sales_coach import concurrency
from sales_coach.routes import GET_ROUTES, POST_ROUTES
from sales_coach.server import AppHandler


@pytest.fixture
def one_slot(monkeypatch):
    monkeypatch.setattr(concurrency, "_analysis_slots", BoundedSemaphore(1))


def test_slot_rejects_when_capacity_is_exhausted(one_slot):
    with concurrency.heavy_analysis_slot(timeout=0.01):
        with pytest.raises(concurrency.AnalysisCapacityExceeded):
            with concurrency.heavy_analysis_slot(timeout=0.01):
                pass
    with concurrency.heavy_analysis_slot(timeout=0.01):
        pass


def test_slot_is_released_when_the_analysis_fails(one_slot):
    with pytest.raises(ValueError):
        with concurrency.heavy_analysis_slot(timeout=0.01):
            raise ValueError("boom")
    with concurrency.heavy_analysis_slot(timeout=0.01):
        pass


def test_heavy_route_answers_503_when_queue_times_out(one_slot, monkeypatch):
    monkeypatch.setattr(concurrency, "ANALYSIS_QUEUE_TIMEOUT_SECONDS", 0.01)
    handler = AppHandler.__new__(AppHandler)
    sent = []
    handler.send_json = lambda payload, status=200: sent.append((status, payload))
    calls = []
    with concurrency.heavy_analysis_slot(timeout=0.01):
        handler._dispatch(POST_ROUTES["/api/analyze-dynamic"], lambda: calls.append("ran"))
    assert calls == []
    assert sent and sent[0][0] == 503


def test_light_route_is_not_limited(one_slot):
    handler = AppHandler.__new__(AppHandler)
    calls = []
    with concurrency.heavy_analysis_slot(timeout=0.01):
        handler._dispatch(GET_ROUTES["/api/auth/me"], lambda: calls.append("ran"))
    assert calls == ["ran"]
