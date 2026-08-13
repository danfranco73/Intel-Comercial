from __future__ import annotations

import mongomock

from security.auth import (
    authenticate,
    bootstrap_admin,
    create_user,
    ensure_auth_indexes,
    issue_csrf_token,
    reset_user_password,
    resolve_session,
    revoke_session,
    set_user_active,
    verify_csrf,
)
from sales_coach.repositories.auth_repository import AuthRepository
from sales_coach.services.auth_service import AuthService


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


def test_deactivating_user_revokes_sessions_and_blocks_login():
    db = mongomock.MongoClient().db
    ensure_auth_indexes(db)
    created = create_user(db, {
        "email": "vendedor@example.test",
        "name": "Vendedor",
        "password": "ClaveSegura123",
        "role": "seller",
        "seller_key": "38",
    })

    context, token = authenticate(db, "vendedor@example.test", "ClaveSegura123", "127.0.0.1")
    assert resolve_session(db, f"codenoa_session={token}") is not None

    result = set_user_active(db, created["id"], False)
    assert result["is_active"] is False

    # La sesión activa se revoca inmediatamente al desactivar.
    assert resolve_session(db, f"codenoa_session={token}") is None

    # Ya no puede volver a iniciar sesión mientras esté desactivado.
    try:
        authenticate(db, "vendedor@example.test", "ClaveSegura123", "127.0.0.1")
    except PermissionError:
        pass
    else:
        raise AssertionError("Un usuario desactivado no debería poder iniciar sesión")

    reactivated = set_user_active(db, created["id"], True)
    assert reactivated["is_active"] is True
    context2, token2 = authenticate(db, "vendedor@example.test", "ClaveSegura123", "127.0.0.1")
    assert resolve_session(db, f"codenoa_session={token2}") is not None


def test_set_user_active_rejects_unknown_user():
    db = mongomock.MongoClient().db
    ensure_auth_indexes(db)
    try:
        set_user_active(db, "000000000000000000000000", False)
    except ValueError as exc:
        assert "no existe" in str(exc)
    else:
        raise AssertionError("Debe fallar si el usuario no existe")


def test_auth_service_blocks_admin_self_deactivation():
    db = mongomock.MongoClient().db
    ensure_auth_indexes(db)
    admin = bootstrap_admin(db, "admin@example.test", "ClaveSegura123")
    service = AuthService(AuthRepository(db))
    try:
        service.set_user_active({"user_id": admin["id"], "is_active": False}, actor_id=admin["id"])
    except ValueError as exc:
        assert "propio usuario" in str(exc)
    else:
        raise AssertionError("Un admin no debería poder desactivarse a sí mismo")

    other = create_user(db, {
        "email": "otro@example.test",
        "name": "Otro Admin",
        "password": "ClaveSegura123",
        "role": "admin",
    })
    result = service.set_user_active({"user_id": other["id"], "is_active": False}, actor_id=admin["id"])
    assert result["is_active"] is False


def test_reset_password_revokes_sessions_and_allows_login_with_new_password():
    db = mongomock.MongoClient().db
    ensure_auth_indexes(db)
    created = create_user(db, {
        "email": "vendedor@example.test",
        "name": "Vendedor",
        "password": "ClaveVieja123",
        "role": "seller",
        "seller_key": "38",
    })
    context, token = authenticate(db, "vendedor@example.test", "ClaveVieja123", "127.0.0.1")
    assert resolve_session(db, f"codenoa_session={token}") is not None

    result = reset_user_password(db, created["id"], "ClaveNueva456")
    assert result["email"] == "vendedor@example.test"

    # La sesión previa queda revocada de inmediato.
    assert resolve_session(db, f"codenoa_session={token}") is None

    # La contraseña vieja ya no sirve; la nueva sí.
    try:
        authenticate(db, "vendedor@example.test", "ClaveVieja123", "127.0.0.1")
    except PermissionError:
        pass
    else:
        raise AssertionError("La contraseña vieja no debería seguir funcionando")

    context2, token2 = authenticate(db, "vendedor@example.test", "ClaveNueva456", "127.0.0.1")
    assert resolve_session(db, f"codenoa_session={token2}") is not None


def test_reset_password_enforces_password_policy():
    db = mongomock.MongoClient().db
    ensure_auth_indexes(db)
    created = create_user(db, {
        "email": "vendedor@example.test",
        "name": "Vendedor",
        "password": "ClaveVieja123",
        "role": "seller",
        "seller_key": "38",
    })
    try:
        reset_user_password(db, created["id"], "corta1A")
    except ValueError as exc:
        assert "al menos" in str(exc)
    else:
        raise AssertionError("Una contraseña que no cumple la política debe rechazarse")


def test_reset_password_rejects_unknown_user():
    db = mongomock.MongoClient().db
    ensure_auth_indexes(db)
    try:
        reset_user_password(db, "000000000000000000000000", "ClaveNueva456")
    except ValueError as exc:
        assert "no existe" in str(exc)
    else:
        raise AssertionError("Debe fallar si el usuario no existe")


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
