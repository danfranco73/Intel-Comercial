from __future__ import annotations

from datetime import date
from typing import Any

from clickhouse_client import get_clickhouse_sales_aggregate
from mongo_client import ERP_SALES_COLLECTION


class ReconciliationService:
    def __init__(self, db):
        self.db = db

    def reconcile_sales(self, fecha_desde: str, fecha_hasta: str) -> dict[str, Any]:
        self._validate_range(fecha_desde, fecha_hasta)
        mongo = self._mongo_aggregate(fecha_desde, fecha_hasta)
        try:
            clickhouse = get_clickhouse_sales_aggregate(fecha_desde, fecha_hasta)
        except Exception as exc:
            clickhouse = {
                "available": False,
                "records": None,
                "amountNet": None,
                "quantity": None,
                "error": str(exc),
            }

        comparable = bool(mongo["available"] and clickhouse.get("available"))
        record_delta = (
            int(mongo["records"]) - int(clickhouse["records"]) if comparable else None
        )
        amount_delta = (
            round(float(mongo["amountNet"]) - float(clickhouse["amountNet"]), 2)
            if comparable
            else None
        )
        quantity_delta = (
            round(float(mongo["quantity"]) - float(clickhouse["quantity"]), 6)
            if comparable
            else None
        )
        consistent = (
            comparable
            and record_delta == 0
            and abs(amount_delta or 0) <= 0.01
            and abs(quantity_delta or 0) <= 0.000001
        )
        return {
            "range": {"fechaDesde": fecha_desde, "fechaHasta": fecha_hasta},
            "mongo": mongo,
            "clickhouse": clickhouse,
            "comparable": comparable,
            "consistent": consistent if comparable else None,
            "difference": {
                "records": record_delta,
                "amountNet": amount_delta,
                "quantity": quantity_delta,
            },
        }

    def _mongo_aggregate(self, fecha_desde: str, fecha_hasta: str) -> dict[str, Any]:
        if self.db is None:
            return {
                "available": False,
                "records": None,
                "amountNet": None,
                "quantity": None,
                "error": "MongoDB no está configurado",
            }
        rows = list(
            self.db[ERP_SALES_COLLECTION].aggregate(
                [
                    {"$match": {"date": {"$gte": fecha_desde, "$lte": fecha_hasta}}},
                    {
                        "$group": {
                            "_id": None,
                            "records": {"$sum": 1},
                            "amountNet": {"$sum": "$amount_net"},
                            "quantity": {"$sum": "$quantity"},
                        }
                    },
                ]
            )
        )
        row = rows[0] if rows else {}
        return {
            "available": True,
            "records": int(row.get("records") or 0),
            "amountNet": round(float(row.get("amountNet") or 0), 2),
            "quantity": round(float(row.get("quantity") or 0), 6),
        }

    @staticmethod
    def _validate_range(fecha_desde: str, fecha_hasta: str) -> None:
        start = date.fromisoformat(fecha_desde)
        end = date.fromisoformat(fecha_hasta)
        if start > end:
            raise ValueError("fechaDesde no puede ser posterior a fechaHasta")
