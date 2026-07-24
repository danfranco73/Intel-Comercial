from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any


_LOGGER = logging.getLogger("codenoa.security")
_LOCK = Lock()


def configure_security_logging(log_dir: Path) -> None:
    with _LOCK:
        if _LOGGER.handlers:
            return
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "security.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        _LOGGER.addHandler(handler)
        _LOGGER.setLevel(logging.INFO)
        _LOGGER.propagate = False


def audit_security_event(
    event: str,
    *,
    outcome: str,
    user_id: str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    payload = {
        "event": event,
        "outcome": outcome,
        "userId": user_id,
        "ipAddress": ip_address,
        "details": _safe_details(details or {}),
    }
    _LOGGER.info(json.dumps(payload, ensure_ascii=False, default=str))


def _safe_details(details: dict[str, Any]) -> dict[str, Any]:
    forbidden = {"password", "password_hash", "cookie", "token", "csrf", "secret"}
    return {
        str(key): value
        for key, value in details.items()
        if str(key).lower() not in forbidden
    }
