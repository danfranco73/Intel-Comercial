from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


ALLOWED_REPORT_TYPES = {"general", "seller_packet"}
ALLOWED_FILTERS = {
    "sales_force",
    "seller_name",
    "supplier",
    "brand",
    "family",
    "branch_key",
    "supervisor_key",
}


@dataclass(frozen=True)
class MeetingCreateRequest:
    report_type: str
    fecha_desde: str
    fecha_hasta: str
    seller_keys: tuple[str, ...]
    filters: dict[str, list[str]]

    @classmethod
    def parse(cls, payload: Any) -> "MeetingCreateRequest":
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        report_type = str(payload.get("report_type") or "general").strip()
        if report_type not in ALLOWED_REPORT_TYPES:
            raise ValueError("report_type inválido")
        start = str(payload.get("fechaDesde") or "").strip()
        end = str(payload.get("fechaHasta") or "").strip()
        try:
            start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ValueError("Las fechas deben tener formato YYYY-MM-DD") from exc
        if start_date > end_date:
            raise ValueError("fechaDesde no puede ser posterior a fechaHasta")
        raw_keys = payload.get("seller_keys") or []
        if not isinstance(raw_keys, list):
            raise ValueError("seller_keys debe ser una lista")
        seller_keys = tuple(dict.fromkeys(str(value).strip() for value in raw_keys if str(value).strip()))
        raw_filters = payload.get("filters") or {}
        if not isinstance(raw_filters, dict):
            raise ValueError("filters debe ser un objeto")
        filters: dict[str, list[str]] = {}
        for field, values in raw_filters.items():
            if field not in ALLOWED_FILTERS:
                raise ValueError(f"Filtro no permitido: {field}")
            items = values if isinstance(values, list) else [values]
            cleaned = list(dict.fromkeys(str(value).strip() for value in items if str(value).strip()))
            if cleaned:
                filters[field] = cleaned
        return cls(report_type, start, end, seller_keys, filters)


@dataclass(frozen=True)
class MeetingPdfRequest:
    report_id: str

    @classmethod
    def parse(cls, payload: Any) -> "MeetingPdfRequest":
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        report_id = str(payload.get("report_id") or "").strip()
        if not report_id:
            raise ValueError("report_id es obligatorio")
        return cls(report_id)
