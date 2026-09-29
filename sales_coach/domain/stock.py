"""Preserve Chess physical stock observations, including unknown dimensions."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation


def identifier(value):
    if isinstance(value, bool) or value is None or not str(value).strip():
        raise ValueError("Identificador requerido")
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError("Identificador Chess debe ser numérico")
    return str(int(text))


def normalize_stock_row(row, deposit_id):
    if not isinstance(row, dict):
        raise ValueError("Fila stock inválida")
    if identifier(row.get("idDeposito")) != identifier(deposit_id):
        raise ValueError("Depósito de respuesta distinto al solicitado")
    for field in ("cantBultos", "cantUnidades"):
        try:
            if isinstance(row.get(field), bool) or not isinstance(row.get(field), (int, float)):
                raise ValueError("Cantidad no numérica")
            if not Decimal(str(row[field])).is_finite():
                raise ValueError("Cantidad no finita")
        except (InvalidOperation, KeyError) as exc:
            raise ValueError("Cantidad inválida") from exc
    dates = {}
    for source, target in (("fecha", "last_movement_date"), ("fecVtoLote", "expiry_date")):
        value = row.get(source)
        dates[target] = date.fromisoformat(str(value)).isoformat() if value not in (None, "") else None
    return {"physical_article_id": identifier(row.get("idArticulo")),
            "deposit_id": identifier(row["idDeposito"]),
            "warehouse_id": identifier(row["idAlmacen"]) if row.get("idAlmacen") is not None else None,
            "article_name": str(row.get("dsArticulo") or ""),
            "packs": row["cantBultos"], "units": row["cantUnidades"], **dates,
            "raw": deepcopy(row)}
