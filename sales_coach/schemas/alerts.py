from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


VALID_ALERT_STATUSES = {
    "new",
    "assigned",
    "in_progress",
    "resolved",
    "dismissed",
    "overdue",
}
VALID_ALERT_SEVERITIES = {"info", "warning", "critical"}


@dataclass(frozen=True)
class AlertGenerateRequest:
    fecha_desde: str
    fecha_hasta: str

    @classmethod
    def parse(cls, payload: Any) -> "AlertGenerateRequest":
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        start = str(payload.get("fechaDesde") or "").strip()
        end = str(payload.get("fechaHasta") or "").strip()
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError as exc:
            raise ValueError("fechaDesde y fechaHasta deben tener formato YYYY-MM-DD") from exc
        if start_date > end_date:
            raise ValueError("fechaDesde no puede ser posterior a fechaHasta")
        return cls(start, end)


@dataclass(frozen=True)
class AlertAssignRequest:
    alert_id: str
    assignee_user_id: str | None
    due_date: str | None
    comment: str | None

    @classmethod
    def parse(cls, payload: Any) -> "AlertAssignRequest":
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        alert_id = _required_text(payload, "alert_id")
        assignee = str(payload.get("assignee_user_id") or "").strip() or None
        due_date = _optional_date(payload.get("due_date"))
        comment = _limited_text(payload.get("comment"), "comment", 1000)
        return cls(alert_id, assignee, due_date, comment)


@dataclass(frozen=True)
class AlertTransitionRequest:
    alert_id: str
    status: str
    comment: str | None
    result: str | None

    @classmethod
    def parse(cls, payload: Any) -> "AlertTransitionRequest":
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        alert_id = _required_text(payload, "alert_id")
        status = _required_text(payload, "status")
        if status not in VALID_ALERT_STATUSES:
            raise ValueError("status inválido")
        comment = _limited_text(payload.get("comment"), "comment", 1000)
        result = _limited_text(payload.get("result"), "result", 2000)
        if status == "resolved" and not result:
            raise ValueError("result es obligatorio al resolver una alerta")
        if status == "dismissed" and not comment:
            raise ValueError("comment es obligatorio al descartar una alerta")
        return cls(alert_id, status, comment, result)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} es obligatorio")
    return value


def _limited_text(value: Any, field: str, limit: int) -> str | None:
    text = str(value or "").strip() or None
    if text and len(text) > limit:
        raise ValueError(f"{field} supera el máximo de {limit} caracteres")
    return text


def _optional_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("due_date debe tener formato YYYY-MM-DD") from exc
    return text
