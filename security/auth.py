from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.cookies import SimpleCookie
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from security.models import AuthContext, AuthenticatedUser, VALID_ROLES
from security.passwords import hash_password, verify_password


SESSION_COOKIE_NAME = "codenoa_session"
DEFAULT_SESSION_HOURS = 12
DEFAULT_LOGIN_WINDOW_MINUTES = 15
DEFAULT_LOGIN_MAX_ATTEMPTS = 5


def ensure_auth_indexes(db: Any) -> None:
    db["users"].create_index([("email", ASCENDING)], unique=True, name="users_email_unique")
    db["users"].create_index([("role", ASCENDING), ("is_active", ASCENDING)], name="users_role_active")
    db["users"].create_index([("seller_key", ASCENDING)], sparse=True, name="users_seller_key")
    db["users"].create_index([("company_key", ASCENDING)], sparse=True, name="users_company_key")
    db["user_sessions"].create_index([("token_hash", ASCENDING)], unique=True, name="sessions_token_unique")
    db["user_sessions"].create_index([("user_id", ASCENDING), ("revoked_at", ASCENDING)], name="sessions_user_active")
    db["user_sessions"].create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="sessions_expiry_ttl")
    db["login_attempts"].create_index(
        [("email", ASCENDING), ("ip_address", ASCENDING), ("created_at", DESCENDING)],
        name="login_attempt_lookup",
    )
    db["login_attempts"].create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="login_attempt_expiry_ttl",
    )


def bootstrap_admin(db: Any, email: str, password: str, name: str = "Administrador") -> dict[str, Any]:
    normalized_email = normalize_email(email)
    if not normalized_email or not password:
        raise ValueError("El bootstrap requiere email y contraseña explícitos")
    ensure_auth_indexes(db)
    now = datetime.now(timezone.utc)
    document = {
        "email": normalized_email,
        "name": name.strip() or "Administrador",
        "password_hash": hash_password(password),
        "role": "admin",
        "seller_key": None,
        "supervisor_key": None,
        "branch_keys": [],
        "sales_force_keys": [],
        "company_key": None,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
    }
    try:
        result = db["users"].insert_one(document)
    except DuplicateKeyError as exc:
        raise ValueError("Ya existe un usuario con ese email; el bootstrap no lo modificó") from exc
    return {"id": str(result.inserted_id), "email": normalized_email, "role": "admin"}


def create_user(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    role = str(payload.get("role") or "")
    if role not in VALID_ROLES:
        raise ValueError("Rol inválido")
    now = datetime.now(timezone.utc)
    document = {
        "email": normalize_email(payload.get("email")),
        "name": str(payload.get("name") or "").strip(),
        "password_hash": hash_password(str(payload.get("password") or "")),
        "role": role,
        "seller_key": _optional_text(payload.get("seller_key")),
        "supervisor_key": _optional_text(payload.get("supervisor_key")),
        "branch_keys": _string_list(payload.get("branch_keys")),
        "sales_force_keys": _string_list(payload.get("sales_force_keys")),
        "company_key": _optional_text(payload.get("company_key")),
        "is_active": bool(payload.get("is_active", True)),
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
    }
    if not document["email"] or not document["name"]:
        raise ValueError("Email y nombre son obligatorios")
    try:
        result = db["users"].insert_one(document)
    except DuplicateKeyError as exc:
        raise ValueError("Ya existe un usuario con ese email") from exc
    return {"id": str(result.inserted_id), "email": document["email"], "role": role}


def authenticate(
    db: Any,
    email: str,
    password: str,
    ip_address: str,
    user_agent: str = "",
) -> tuple[AuthContext, str]:
    ensure_auth_indexes(db)
    normalized_email = normalize_email(email)
    if is_login_blocked(db, normalized_email, ip_address):
        raise PermissionError("Demasiados intentos. Esperá antes de volver a intentar")

    user_document = db["users"].find_one({"email": normalized_email, "is_active": True})
    valid = bool(user_document and verify_password(password, user_document.get("password_hash", "")))
    record_login_attempt(db, normalized_email, ip_address, valid)
    if not valid:
        raise PermissionError("Credenciales inválidas")

    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    session_id = ObjectId()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=session_hours())
    db["user_sessions"].insert_one(
        {
            "_id": session_id,
            "user_id": user_document["_id"],
            "token_hash": _hash_token(token),
            "csrf_hash": _hash_token(csrf_token),
            "csrf_token": csrf_token,
            "created_at": now,
            "expires_at": expires_at,
            "last_seen_at": now,
            "revoked_at": None,
            "ip_address": ip_address,
            "user_agent": user_agent[:300],
        }
    )
    db["users"].update_one({"_id": user_document["_id"]}, {"$set": {"last_login_at": now}})
    context = AuthContext(
        user=AuthenticatedUser.from_document(user_document),
        session_id=str(session_id),
        csrf_token=csrf_token,
    )
    return context, token


def resolve_session(db: Any, cookie_header: str | None) -> AuthContext | None:
    token = read_session_cookie(cookie_header)
    if not token:
        return None
    now = datetime.now(timezone.utc)
    session = db["user_sessions"].find_one(
        {
            "token_hash": _hash_token(token),
            "revoked_at": None,
            "expires_at": {"$gt": now},
        }
    )
    if not session:
        return None
    user_document = db["users"].find_one({"_id": session["user_id"], "is_active": True})
    if not user_document:
        return None
    db["user_sessions"].update_one({"_id": session["_id"]}, {"$set": {"last_seen_at": now}})
    return AuthContext(
        user=AuthenticatedUser.from_document(user_document),
        session_id=str(session["_id"]),
        csrf_token="",
    )


def issue_csrf_token(db: Any, context: AuthContext) -> str:
    session_id = ObjectId(context.session_id)
    session = db["user_sessions"].find_one(
        {"_id": session_id, "revoked_at": None},
        {"csrf_token": 1},
    )
    existing = str((session or {}).get("csrf_token") or "")
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    db["user_sessions"].update_one(
        {"_id": session_id, "revoked_at": None},
        {"$set": {"csrf_hash": _hash_token(token), "csrf_token": token}},
    )
    return token


def verify_csrf(db: Any, context: AuthContext, provided_token: str | None) -> bool:
    if not provided_token:
        return False
    try:
        session_id = ObjectId(context.session_id)
    except Exception:
        return False
    session = db["user_sessions"].find_one({"_id": session_id, "revoked_at": None}, {"csrf_hash": 1})
    expected = str((session or {}).get("csrf_hash") or "")
    return bool(expected and secrets.compare_digest(expected, _hash_token(provided_token)))


def revoke_session(db: Any, context: AuthContext) -> None:
    try:
        session_id = ObjectId(context.session_id)
    except Exception:
        return
    db["user_sessions"].update_one(
        {"_id": session_id},
        {"$set": {"revoked_at": datetime.now(timezone.utc)}},
    )


def build_session_cookie(token: str) -> str:
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = token
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    morsel["max-age"] = session_hours() * 3600
    morsel["expires"] = format_datetime(
        datetime.now(timezone.utc) + timedelta(hours=session_hours()),
        usegmt=True,
    )
    if secure_cookies():
        morsel["secure"] = True
    return morsel.OutputString()


def build_clear_cookie() -> str:
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = ""
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    morsel["max-age"] = 0
    morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    if secure_cookies():
        morsel["secure"] = True
    return morsel.OutputString()


def read_session_cookie(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return None
    morsel = cookie.get(SESSION_COOKIE_NAME)
    return morsel.value if morsel else None


def is_login_blocked(db: Any, email: str, ip_address: str) -> bool:
    since = datetime.now(timezone.utc) - timedelta(minutes=login_window_minutes())
    failures = db["login_attempts"].count_documents(
        {
            "email": email,
            "ip_address": ip_address,
            "success": False,
            "created_at": {"$gte": since},
        }
    )
    return failures >= login_max_attempts()


def record_login_attempt(db: Any, email: str, ip_address: str, success: bool) -> None:
    now = datetime.now(timezone.utc)
    db["login_attempts"].insert_one(
        {
            "email": email,
            "ip_address": ip_address,
            "success": success,
            "created_at": now,
            "expires_at": now + timedelta(days=2),
        }
    )


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def session_hours() -> int:
    return _positive_int_env("APP_SESSION_HOURS", DEFAULT_SESSION_HOURS)


def login_window_minutes() -> int:
    return _positive_int_env("APP_LOGIN_WINDOW_MINUTES", DEFAULT_LOGIN_WINDOW_MINUTES)


def login_max_attempts() -> int:
    return _positive_int_env("APP_LOGIN_MAX_ATTEMPTS", DEFAULT_LOGIN_MAX_ATTEMPTS)


def secure_cookies() -> bool:
    raw = os.getenv("APP_COOKIE_SECURE")
    if raw is not None:
        return raw.strip().lower() not in {"0", "false", "no"}
    return os.getenv("APP_ENV", "development").strip().lower() == "production"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name) or default))
    except (TypeError, ValueError):
        return default


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
