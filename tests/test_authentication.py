from __future__ import annotations

import mongomock

from security.auth import (
    authenticate,
    bootstrap_admin,
    ensure_auth_indexes,
    issue_csrf_token,
    resolve_session,
    revoke_session,
    verify_csrf,
)


def test_login_session_csrf_and_logout(monkeypatch):
    monkeypatch.setenv("APP_SESSION_HOURS", "1")
    db = mongomock.MongoClient().db
    ensure_auth_indexes(db)
    bootstrap_admin(db, "admin@example.test", "ClaveSegura123", "Admin")

    context, token = authenticate(db, "ADMIN@example.test", "ClaveSegura123", "127.0.0.1")
    resolved = resolve_session(db, f"codenoa_session={token}")
    assert resolved is not None
    assert resolved.user.role == "admin"
    assert verify_csrf(db, resolved, context.csrf_token)
    assert issue_csrf_token(db, resolved) == context.csrf_token
    assert issue_csrf_token(db, resolved) == context.csrf_token
    assert not verify_csrf(db, resolved, "csrf-falso")

    revoke_session(db, resolved)
    assert resolve_session(db, f"codenoa_session={token}") is None


def test_invalid_password_is_rejected():
    db = mongomock.MongoClient().db
    bootstrap_admin(db, "admin@example.test", "ClaveSegura123")
    try:
        authenticate(db, "admin@example.test", "incorrecta", "127.0.0.1")
    except PermissionError:
        pass
    else:
        raise AssertionError("La contraseña inválida debe rechazarse")


def test_brute_force_limit_blocks_repeated_failures(monkeypatch):
    monkeypatch.setenv("APP_LOGIN_MAX_ATTEMPTS", "2")
    db = mongomock.MongoClient().db
    bootstrap_admin(db, "admin@example.test", "ClaveSegura123")
    for _ in range(2):
        try:
            authenticate(db, "admin@example.test", "incorrecta", "127.0.0.1")
        except PermissionError:
            pass
    try:
        authenticate(db, "admin@example.test", "ClaveSegura123", "127.0.0.1")
    except PermissionError as exc:
        assert "Demasiados intentos" in str(exc)
    else:
        raise AssertionError("El límite de fuerza bruta debe bloquear el login")
