import threading
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, build_opener

import mongomock
import sales_coach.server as app
from security.auth import bootstrap_admin, create_user
from sales_coach.repositories.sales_repository import SalesRepository
from scripts.migrate_intelligence_phase1 import migrate
from test_http_security import request_json


def test_intelligence_http_auth_csrf_validation_and_contracts(monkeypatch):
    db = mongomock.MongoClient().db
    migrate(db)
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(SalesRepository, "load_intelligence", lambda *_: ([], "mongo", []))
    bootstrap_admin(db, "admin@example.test", "ClaveSegura123")
    create_user(db, {"email": "viewer@example.test", "name": "Viewer", "password": "ClaveSegura123", "role": "viewer"})
    server = app.ReusableHTTPServer(("127.0.0.1", 0), app.AppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        anonymous = build_opener(HTTPCookieProcessor(CookieJar()))
        for path in ("sales/daily-brief", "sales/drilldown", "stock/daily-brief", "stock/items", "deposits"):
            assert request_json(anonymous, base + "/api/intelligence/" + path)[0] == 401
        admin = build_opener(HTTPCookieProcessor(CookieJar()))
        _, login = request_json(admin, base + "/api/auth/login", "POST",
            {"email": "admin@example.test", "password": "ClaveSegura123"})
        csrf = login["csrfToken"]
        for path in ("deposits", "deposits/discover", "deposits/universe", "stock/sync"):
            assert request_json(admin, base + "/api/intelligence/" + path, "POST", {})[0] == 403
        status, brief = request_json(admin, base + "/api/intelligence/sales/daily-brief?as_of=2026-06-30")
        assert status == 200 and brief["status"] == "unavailable"
        assert set(("summary", "deviations", "drilldowns", "data_quality")) <= brief.keys()
        assert request_json(admin, base + "/api/intelligence/sales/drilldown?dimension=client&dimension=seller")[0] == 400
        status, catalog = request_json(admin, base + "/api/intelligence/deposits")
        assert status == 200 and len(catalog["deposits"]) == 9
        assert all(d["active"] is None for d in catalog["deposits"])
        assert request_json(admin, base + "/api/intelligence/deposits/discover", "POST", {}, csrf)[0] == 200
        assert request_json(admin, base + "/api/intelligence/stock/sync", "POST", {"deposit_id": "1"}, csrf)[0] == 400
        status, snapshot = request_json(admin, base + "/api/intelligence/stock/sync", "POST", {"stock_date": "2026-06-30"}, csrf)
        assert status == 200 and snapshot["status"] == "unavailable" and snapshot["expected_deposit_ids"] == []
        viewer = build_opener(HTTPCookieProcessor(CookieJar()))
        _, login = request_json(viewer, base + "/api/auth/login", "POST",
            {"email": "viewer@example.test", "password": "ClaveSegura123"})
        assert request_json(viewer, base + "/api/intelligence/stock/daily-brief")[0] == 403
        assert request_json(viewer, base + "/api/intelligence/deposits")[0] == 403
        assert request_json(viewer, base + "/api/intelligence/deposits", "POST", {}, login["csrfToken"])[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
