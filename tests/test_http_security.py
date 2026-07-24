from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import mongomock

import app
import sales_coach.server
from security.auth import bootstrap_admin, create_user


def test_upload_file_listing_does_not_close_the_connection():
    result = sales_coach.server.list_available_files(scope="uploads")

    assert isinstance(result, list)


def request_json(opener, url, method="GET", payload=None, csrf=None):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = Request(url, data=data, method=method, headers=headers)
    try:
        response = opener.open(request, timeout=5)
        return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_sensitive_api_requires_login_role_and_csrf(monkeypatch):
    db = mongomock.MongoClient().db
    monkeypatch.setattr(sales_coach.server, "get_db", lambda: db)
    bootstrap_admin(db, "admin@example.test", "ClaveSegura123")
    create_user(
        db,
        {
            "email": "seller@example.test",
            "name": "Vendedora",
            "password": "ClaveSegura123",
            "role": "seller",
            "seller_key": "S1",
        },
    )

    server = app.ReusableHTTPServer(("127.0.0.1", 0), app.AppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        anonymous = build_opener(HTTPCookieProcessor(CookieJar()))
        status, _ = request_json(anonymous, f"{base_url}/api/datasets")
        assert status == 401

        admin = build_opener(HTTPCookieProcessor(CookieJar()))
        status, login = request_json(
            admin,
            f"{base_url}/api/auth/login",
            method="POST",
            payload={"email": "admin@example.test", "password": "ClaveSegura123"},
        )
        assert status == 200
        csrf = login["csrfToken"]

        status, _ = request_json(
            admin,
            f"{base_url}/api/session",
            method="POST",
            payload={"datasets": {}},
        )
        assert status == 403
        status, body = request_json(
            admin,
            f"{base_url}/api/session",
            method="POST",
            payload={"datasets": {}},
            csrf=csrf,
        )
        assert status == 200
        assert body["saved"]

        seller = build_opener(HTTPCookieProcessor(CookieJar()))
        status, seller_login = request_json(
            seller,
            f"{base_url}/api/auth/login",
            method="POST",
            payload={"email": "seller@example.test", "password": "ClaveSegura123"},
        )
        assert status == 200
        status, _ = request_json(seller, f"{base_url}/api/users")
        assert status == 403
        status, _ = request_json(
            seller,
            f"{base_url}/api/users",
            method="POST",
            payload={
                "email": "forbidden@example.test",
                "name": "No autorizado",
                "password": "ClaveSegura123",
                "role": "admin",
            },
            csrf=seller_login["csrfToken"],
        )
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
