from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from security.models import VALID_ROLES


def _require_mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("El payload debe ser un objeto JSON")
    return payload


@dataclass(frozen=True)
class LoginRequest:
    email: str
    password: str

    @classmethod
    def parse(cls, payload: Any) -> "LoginRequest":
        data = _require_mapping(payload)
        email = str(data.get("email") or "").strip().lower()
        password = str(data.get("password") or "")
        if not email or "@" not in email:
            raise ValueError("Ingresá un email válido")
        if not password:
            raise ValueError("La contraseña es obligatoria")
        return cls(email=email, password=password)


@dataclass(frozen=True)
class UserCreateRequest:
    email: str
    name: str
    password: str
    role: str
    seller_key: str | None
    supervisor_key: str | None
    branch_keys: tuple[str, ...]
    sales_force_keys: tuple[str, ...]
    company_key: str | None
    is_active: bool

    @classmethod
    def parse(cls, payload: Any) -> "UserCreateRequest":
        data = _require_mapping(payload)
        role = str(data.get("role") or "").strip()
        if role not in VALID_ROLES:
            raise ValueError("Rol inválido")
        email = str(data.get("email") or "").strip().lower()
        name = str(data.get("name") or "").strip()
        password = str(data.get("password") or "")
        if not email or "@" not in email or not name or not password:
            raise ValueError("Email, nombre y contraseña son obligatorios")
        return cls(
            email=email,
            name=name,
            password=password,
            role=role,
            seller_key=_optional(data.get("seller_key")),
            supervisor_key=_optional(data.get("supervisor_key")),
            branch_keys=tuple(_strings(data.get("branch_keys"))),
            sales_force_keys=tuple(_strings(data.get("sales_force_keys"))),
            company_key=_optional(data.get("company_key")),
            is_active=bool(data.get("is_active", True)),
        )

    def as_repository_payload(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "name": self.name,
            "password": self.password,
            "role": self.role,
            "seller_key": self.seller_key,
            "supervisor_key": self.supervisor_key,
            "branch_keys": list(self.branch_keys),
            "sales_force_keys": list(self.sales_force_keys),
            "company_key": self.company_key,
            "is_active": self.is_active,
        }


@dataclass(frozen=True)
class SyncRequest:
    fecha_desde: str
    fecha_hasta: str
    refresh_masters: bool
    force_refresh_sales: bool

    @classmethod
    def parse(cls, payload: Any) -> "SyncRequest":
        data = _require_mapping(payload)
        start = _iso_date(data.get("fechaDesde"), "fechaDesde")
        end = _iso_date(data.get("fechaHasta"), "fechaHasta")
        if start > end:
            raise ValueError("fechaDesde no puede ser mayor que fechaHasta")
        return cls(
            fecha_desde=start.isoformat(),
            fecha_hasta=end.isoformat(),
            refresh_masters=bool(data.get("refreshMasters")),
            force_refresh_sales=bool(data.get("forceRefreshSales")),
        )


@dataclass(frozen=True)
class AnalysisRequest:
    datasets: dict[str, Any]
    filters: dict[str, Any]
    supplier_focus: Any
    planning: Any

    @classmethod
    def parse(cls, payload: Any) -> "AnalysisRequest":
        data = _require_mapping(payload)
        datasets = data.get("datasets")
        if not isinstance(datasets, dict) or not datasets:
            raise ValueError("No llegaron datasets para analizar")
        filters = data.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError("filters debe ser un objeto")
        return cls(
            datasets=datasets,
            filters=filters,
            supplier_focus=data.get("supplierFocus"),
            planning=data.get("planning"),
        )


def _iso_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} debe tener formato YYYY-MM-DD") from exc


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
