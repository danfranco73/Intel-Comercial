from __future__ import annotations

import gzip
import json
import re
import threading
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener

import mongomock
import pytest

import app
import sales_coach.server
from sales_coach.cache import TtlCache, cached_status, invalidate_status_cache
from security.auth import bootstrap_admin


@pytest.fixture
def server_url(monkeypatch):
    db = mongomock.MongoClient().db
    monkeypatch.setattr(sales_coach.server, "get_db", lambda: db)
    bootstrap_admin(db, "admin@example.test", "ClaveSegura123")
    server = app.ReusableHTTPServer(("127.0.0.1", 0), app.AppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def logged_in_opener(base_url):
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    request = Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"email": "admin@example.test", "password": "ClaveSegura123"}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener.open(request, timeout=5).read()
    return opener


def test_pages_reference_versioned_assets_that_are_cached_and_gzipped(server_url):
    opener = logged_in_opener(server_url)
    page = opener.open(f"{server_url}/bi", timeout=5)
    assert "no-store" in page.headers["Cache-Control"]
    html = page.read().decode("utf-8")
    match = re.search(r'src="/app\.js\?v=([0-9a-f]{12})"', html)
    assert match
    assert re.search(r'href="/vendor/tabulator/tabulator\.min\.css\?v=[0-9a-f]{12}"', html)

    request = Request(f"{server_url}/app.js?v={match.group(1)}", headers={"Accept-Encoding": "gzip"})
    response = opener.open(request, timeout=5)
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert response.headers["Content-Encoding"] == "gzip"
    body = gzip.decompress(response.read())
    assert body == (sales_coach.server.STATIC_DIR / "app.js").read_bytes()

    plain = opener.open(f"{server_url}/app.js", timeout=5)
    assert plain.headers["Cache-Control"] == "no-cache"
    assert plain.headers.get("Content-Encoding") is None


def test_json_responses_are_gzipped_when_accepted(server_url):
    opener = logged_in_opener(server_url)
    request = Request(f"{server_url}/api/datasets", headers={"Accept-Encoding": "gzip"})
    response = opener.open(request, timeout=5)
    assert response.headers["Content-Encoding"] == "gzip"
    assert "datasets" in json.loads(gzip.decompress(response.read()))


def test_status_cache_reuses_value_until_invalidated():
    calls = []

    def compute():
        calls.append(1)
        return {"count": len(calls)}

    assert cached_status("probe", compute) == {"count": 1}
    assert cached_status("probe", compute) == {"count": 1}
    invalidate_status_cache()
    assert cached_status("probe", compute) == {"count": 2}


def test_status_cache_with_zero_ttl_never_stores():
    cache = TtlCache(0)
    values = iter([1, 2])
    assert cache.get_or_compute("key", lambda: next(values)) == 1
    assert cache.get_or_compute("key", lambda: next(values)) == 2
