from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


VALID_SCOPE_TYPES = {
    "company",
    "branch",
    "sales_force",
    "supervisor",
    "seller",
    "route",
    "channel",
    "brand",
    "family",
}
VALID_OBJECTIVE_METRICS = {
    "net_sales",
    "quantity",
    "active_clients",
    "mix",
    "new_clients",
    "recovered_clients",
}
VALID_OBJECTIVE_STATUSES = {"draft", "active", "closed"}


@dataclass(frozen=True)
class ObjectiveCreateRequest:
    period: str
    scope_type: str
    scope_key: str
    metric: str
    target_value: Decimal
    baseline_value: Decimal | None
    notes: str | None
    company_key: str

    @classmethod
    def parse(cls, payload: Any, default_company_key: str) -> "ObjectiveCreateRequest":
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        period = str(payload.get("period") or "").strip()
        if len(period) != 7 or period[4] != "-":
            raise ValueError("period debe tener formato YYYY-MM")
        try:
            year, month = (int(part) for part in period.split("-"))
        except ValueError as exc:
            raise ValueError("period debe tener formato YYYY-MM") from exc
        if year < 2000 or month < 1 or month > 12:
            raise ValueError("period no es válido")
        scope_type = str(payload.get("scope_type") or "").strip()
        if scope_type not in VALID_SCOPE_TYPES:
            raise ValueError("scope_type inválido")
        scope_key = str(
            payload.get("scope_key")
            or (default_company_key if scope_type == "company" else "")
        ).strip()
        if not scope_key:
            raise ValueError("scope_key es obligatorio")
        metric = str(payload.get("metric") or "").strip()
        if metric not in VALID_OBJECTIVE_METRICS:
            raise ValueError("metric inválida")
        target_value = _decimal(payload.get("target_value"), "target_value")
        if target_value < 0:
            raise ValueError("target_value no puede ser negativo")
        baseline_raw = payload.get("baseline_value")
        baseline_value = (
            None if baseline_raw in (None, "") else _decimal(baseline_raw, "baseline_value")
        )
        notes = str(payload.get("notes") or "").strip() or None
        company_key = str(payload.get("company_key") or default_company_key).strip()
        if not company_key:
            raise ValueError("company_key es obligatorio")
        return cls(
            period=period,
            scope_type=scope_type,
            scope_key=scope_key,
            metric=metric,
            target_value=target_value,
            baseline_value=baseline_value,
            notes=notes,
            company_key=company_key,
        )


@dataclass(frozen=True)
class ObjectiveUpdateRequest:
    objective_id: str
    target_value: Decimal | None
    baseline_value: Decimal | None
    baseline_provided: bool
    notes: str | None
    notes_provided: bool

    @classmethod
    def parse(cls, payload: Any) -> "ObjectiveUpdateRequest":
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        objective_id = str(payload.get("objective_id") or "").strip()
        if not objective_id:
            raise ValueError("objective_id es obligatorio")
        target_value = None
        if "target_value" in payload:
            target_value = _decimal(payload.get("target_value"), "target_value")
            if target_value < 0:
                raise ValueError("target_value no puede ser negativo")
        baseline_provided = "baseline_value" in payload
        baseline_value = None
        if baseline_provided and payload.get("baseline_value") not in (None, ""):
            baseline_value = _decimal(payload.get("baseline_value"), "baseline_value")
        notes_provided = "notes" in payload
        notes = str(payload.get("notes") or "").strip() or None
        if target_value is None and not baseline_provided and not notes_provided:
            raise ValueError("No hay cambios para aplicar")
        return cls(
            objective_id=objective_id,
            target_value=target_value,
            baseline_value=baseline_value,
            baseline_provided=baseline_provided,
            notes=notes,
            notes_provided=notes_provided,
        )


@dataclass(frozen=True)
class ObjectiveTransitionRequest:
    objective_id: str

    @classmethod
    def parse(cls, payload: Any) -> "ObjectiveTransitionRequest":
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        objective_id = str(payload.get("objective_id") or "").strip()
        if not objective_id:
            raise ValueError("objective_id es obligatorio")
        return cls(objective_id)


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} debe ser numérico") from exc
    if not result.is_finite():
        raise ValueError(f"{field} debe ser finito")
    return result
