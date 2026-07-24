from __future__ import annotations

import calendar
import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from pymongo.errors import DuplicateKeyError

from sales_coach.repositories.objective_repository import ObjectiveRepository
from sales_coach.schemas.objectives import (
    ObjectiveCreateRequest,
    ObjectiveTransitionRequest,
    ObjectiveUpdateRequest,
)


DEFAULT_COMPANY_KEY = "CODENOA"
WRITE_ROLES = {"admin", "commercial_director", "supervisor"}
APPROVE_ROLES = {"admin", "commercial_director"}


class ObjectiveService:
    def __init__(self, db):
        self.db = db
        self.repository = ObjectiveRepository(db)
        self.default_company_key = (
            os.getenv("DEFAULT_COMPANY_KEY") or DEFAULT_COMPANY_KEY
        ).strip()

    def create(self, payload: Any, user, data_scope) -> dict[str, Any]:
        request = ObjectiveCreateRequest.parse(payload, self.default_company_key)
        if request.company_key != self.default_company_key:
            raise PermissionError("La empresa solicitada está fuera de tu alcance")
        self._require_write_scope(user, request.scope_type, request.scope_key, data_scope)
        try:
            return self.repository.create(
                {
                    "company_key": request.company_key,
                    "period": request.period,
                    "scope_type": request.scope_type,
                    "scope_key": request.scope_key,
                    "metric": request.metric,
                    "target_value": request.target_value,
                    "baseline_value": request.baseline_value,
                    "notes": request.notes,
                },
                user.id,
            )
        except DuplicateKeyError as exc:
            raise ValueError(
                "Ya existe un objetivo vigente para ese período, alcance y métrica"
            ) from exc

    def update(self, payload: Any, user, data_scope) -> dict[str, Any]:
        request = ObjectiveUpdateRequest.parse(payload)
        current = self._get_authorized(request.objective_id, user, data_scope, write=True)
        return self.repository.revise(
            request.objective_id,
            {
                "target_value": request.target_value,
                "baseline_value": request.baseline_value,
                "baseline_provided": request.baseline_provided,
                "notes": request.notes,
                "notes_provided": request.notes_provided,
            },
            user.id,
        )

    def approve(self, payload: Any, user, data_scope) -> dict[str, Any]:
        request = ObjectiveTransitionRequest.parse(payload)
        if user.role not in APPROVE_ROLES:
            raise PermissionError("Sólo administración o dirección puede aprobar objetivos")
        self._get_authorized(request.objective_id, user, data_scope, write=True)
        return self.repository.transition(
            request.objective_id, "draft", "active", user.id
        )

    def close(self, payload: Any, user, data_scope) -> dict[str, Any]:
        request = ObjectiveTransitionRequest.parse(payload)
        current = self._get_authorized(request.objective_id, user, data_scope, write=True)
        if user.role not in APPROVE_ROLES and current["scope_type"] != "seller":
            raise PermissionError("El supervisor sólo puede cerrar objetivos de vendedores")
        return self.repository.transition(
            request.objective_id, "active", "closed", user.id
        )

    def list(
        self,
        user,
        data_scope,
        *,
        period: str | None = None,
        status: str | None = None,
        include_progress: bool = True,
    ) -> list[dict[str, Any]]:
        query = {
            **self._visibility_query(user, data_scope),
            "company_key": self.default_company_key,
        }
        if period:
            query["period"] = period
        if status:
            query["status"] = status
        objectives = self.repository.list_current(query)
        if not include_progress:
            return objectives
        return [{**item, "progress": self.calculate_progress(item)} for item in objectives]

    def history(self, objective_id: str, user, data_scope) -> list[dict[str, Any]]:
        self._get_authorized(objective_id, user, data_scope)
        return self.repository.history(objective_id)

    def calculate_progress(self, objective: dict[str, Any]) -> dict[str, Any]:
        start, end = self._period_range(objective["period"])
        current_value = self._metric_value(
            objective["metric"],
            start,
            end,
            objective["scope_type"],
            objective["scope_key"],
        )
        target = Decimal(str(objective["target_value"]))
        gap = current_value - target
        fulfillment = (current_value / target * Decimal("100")) if target else Decimal("0")
        today = date.today()
        if start <= today <= end:
            elapsed = (today - start).days + 1
            total = (end - start).days + 1
            projection = current_value / Decimal(elapsed) * Decimal(total)
        elif today > end:
            projection = current_value
        else:
            projection = Decimal("0")
        baseline = (
            Decimal(str(objective["baseline_value"]))
            if objective.get("baseline_value") is not None
            else None
        )
        trend_pct = (
            (current_value - baseline) / abs(baseline) * Decimal("100")
            if baseline not in (None, Decimal("0"))
            else None
        )
        return {
            "actual_value": self._number(current_value),
            "target_value": self._number(target),
            "fulfillment_pct": self._number(fulfillment),
            "gap_value": self._number(gap),
            "projection_value": self._number(projection),
            "trend_pct": self._number(trend_pct) if trend_pct is not None else None,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "calculated_at": date.today().isoformat(),
            "formula": self._formula(objective["metric"]),
        }

    def _metric_value(
        self,
        metric: str,
        start: date,
        end: date,
        scope_type: str,
        scope_key: str,
    ) -> Decimal:
        match = {
            "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
            **self._sales_scope(scope_type, scope_key),
        }
        collection = self.db["erp_sales"]
        if metric in {"net_sales", "quantity"}:
            field = "$amount_net" if metric == "net_sales" else "$quantity"
            rows = list(
                collection.aggregate(
                    [{"$match": match}, {"$group": {"_id": None, "value": {"$sum": field}}}]
                )
            )
            return Decimal(str(rows[0].get("value") or 0)) if rows else Decimal("0")
        if metric == "active_clients":
            return Decimal(collection.distinct("client_key", match).__len__())
        if metric == "mix":
            return Decimal(collection.distinct("product_key", match).__len__())
        current_clients = set(collection.distinct("client_key", match))
        if metric == "new_clients":
            count = 0
            for client_key in current_clients:
                first = collection.find_one(
                    {"client_key": client_key, **self._sales_scope(scope_type, scope_key)},
                    {"date": 1},
                    sort=[("date", 1)],
                )
                if first and start.isoformat() <= first.get("date", "") <= end.isoformat():
                    count += 1
            return Decimal(count)
        if metric == "recovered_clients":
            days = (end - start).days + 1
            previous_end = start - timedelta(days=1)
            previous_start = previous_end - timedelta(days=days - 1)
            scope = self._sales_scope(scope_type, scope_key)
            previous = set(
                collection.distinct(
                    "client_key",
                    {
                        "date": {
                            "$gte": previous_start.isoformat(),
                            "$lte": previous_end.isoformat(),
                        },
                        **scope,
                    },
                )
            )
            historical = set(
                collection.distinct(
                    "client_key",
                    {"date": {"$lt": previous_start.isoformat()}, **scope},
                )
            )
            return Decimal(len((current_clients - previous) & historical))
        raise ValueError("Métrica no soportada")

    def _sales_scope(self, scope_type: str, scope_key: str) -> dict[str, Any]:
        if scope_type == "company":
            return {}
        if scope_type == "seller":
            return {"seller_key": scope_key}
        if scope_type == "sales_force":
            return {"$or": [{"sales_scheme_key": scope_key}, {"sales_force": scope_key}]}
        if scope_type == "route":
            return {"route_description": scope_key}
        if scope_type == "channel":
            return {"channel": scope_key}
        if scope_type in {"branch", "supervisor"}:
            seller_field = "branch_key" if scope_type == "branch" else "supervisor_key"
            seller_keys = [
                row["seller_key"]
                for row in self.db["erp_sellers"].find(
                    {seller_field: scope_key}, {"seller_key": 1}
                )
                if row.get("seller_key")
            ]
            return {"seller_key": {"$in": seller_keys}}
        if scope_type in {"brand", "family"}:
            article_field = "brand" if scope_type == "brand" else "family"
            product_keys = [
                row["product_key"]
                for row in self.db["erp_articles"].find(
                    {article_field: scope_key}, {"product_key": 1}
                )
                if row.get("product_key")
            ]
            return {"product_key": {"$in": product_keys}}
        return {"_id": {"$exists": False}}

    def _get_authorized(self, objective_id, user, data_scope, write=False):
        current = self.repository.get_current(objective_id)
        if current is None:
            raise LookupError("Objetivo no encontrado")
        serialized = self.repository.serialize(current)
        if serialized.get("company_key") != self.default_company_key:
            raise PermissionError("El objetivo está fuera de tu empresa")
        if not self._can_access(user, serialized["scope_type"], serialized["scope_key"], data_scope):
            raise PermissionError("El objetivo está fuera de tu alcance")
        if write:
            self._require_write_scope(
                user, serialized["scope_type"], serialized["scope_key"], data_scope
            )
        return serialized

    def _visibility_query(self, user, data_scope) -> dict[str, Any]:
        if user.role in {"admin", "commercial_director"}:
            return {}
        if user.role == "seller":
            return {"scope_type": "seller", "scope_key": user.seller_key or "__none__"}
        if user.role == "viewer":
            return {"scope_type": "company"}
        allowed_names = set((data_scope or {}).get("seller_name") or [])
        seller_keys = [
            row["seller_key"]
            for row in self.db["erp_sellers"].find(
                {"seller_name": {"$in": list(allowed_names)}}, {"seller_key": 1}
            )
            if row.get("seller_key")
        ]
        clauses: list[dict[str, Any]] = [
            {"scope_type": "seller", "scope_key": {"$in": seller_keys}}
        ]
        if user.supervisor_key:
            clauses.append({"scope_type": "supervisor", "scope_key": user.supervisor_key})
        if user.branch_keys:
            clauses.append({"scope_type": "branch", "scope_key": {"$in": list(user.branch_keys)}})
        if user.sales_force_keys:
            clauses.append(
                {"scope_type": "sales_force", "scope_key": {"$in": list(user.sales_force_keys)}}
            )
        return {"$or": clauses}

    def _require_write_scope(self, user, scope_type, scope_key, data_scope):
        if user.role not in WRITE_ROLES:
            raise PermissionError("Tu rol no puede modificar objetivos")
        if not self._can_access(user, scope_type, scope_key, data_scope):
            raise PermissionError("El objetivo está fuera de tu alcance")
        if user.role == "supervisor" and scope_type not in {
            "seller",
            "supervisor",
            "branch",
            "sales_force",
        }:
            raise PermissionError("El supervisor no puede crear objetivos para ese alcance")

    def _can_access(self, user, scope_type, scope_key, data_scope) -> bool:
        if user.role in {"admin", "commercial_director"}:
            return True
        if user.role == "viewer":
            return scope_type == "company"
        if user.role == "seller":
            return scope_type == "seller" and scope_key == user.seller_key
        if scope_type == "supervisor":
            return bool(user.supervisor_key and scope_key == user.supervisor_key)
        if scope_type == "branch":
            return scope_key in user.branch_keys
        if scope_type == "sales_force":
            return scope_key in user.sales_force_keys
        if scope_type == "seller":
            seller = self.db["erp_sellers"].find_one(
                {"seller_key": scope_key}, {"seller_name": 1}
            )
            return bool(
                seller
                and seller.get("seller_name") in set((data_scope or {}).get("seller_name") or [])
            )
        return False

    @staticmethod
    def _period_range(period: str) -> tuple[date, date]:
        year, month = (int(part) for part in period.split("-"))
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])

    @staticmethod
    def _formula(metric: str) -> str:
        return {
            "net_sales": "SUM(amount_net)",
            "quantity": "SUM(quantity)",
            "active_clients": "COUNT(DISTINCT client_key)",
            "mix": "COUNT(DISTINCT product_key)",
            "new_clients": "clientes cuya primera compra está dentro del período",
            "recovered_clients": "clientes activos, ausentes en período anterior y con compra histórica",
        }[metric]

    @staticmethod
    def _number(value: Decimal) -> float:
        return round(float(value), 6)
