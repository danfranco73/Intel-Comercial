from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId

from sales_coach.repositories.alert_repository import AlertRepository
from sales_coach.schemas.alerts import (
    AlertAssignRequest,
    AlertGenerateRequest,
    AlertTransitionRequest,
)
from sales_coach.services.objective_service import ObjectiveService
from sales_coach.services.sales_coach_service import SalesCoachService


COMPANY_KEY = (os.getenv("DEFAULT_COMPANY_KEY") or "CODENOA").strip()
OPEN_STATUSES = {"new", "assigned", "in_progress", "overdue"}
TRANSITIONS = {
    "new": {"assigned", "in_progress", "dismissed"},
    "assigned": {"in_progress", "resolved", "dismissed"},
    "in_progress": {"assigned", "resolved", "dismissed"},
    "overdue": {"assigned", "in_progress", "resolved", "dismissed"},
    "resolved": set(),
    "dismissed": set(),
}
MANAGE_ROLES = {"admin", "commercial_director", "supervisor", "seller"}


class AlertService:
    def __init__(self, db, sales_coach_service: SalesCoachService | None = None):
        self.db = db
        self.repository = AlertRepository(db)
        self.sales_coach = sales_coach_service or SalesCoachService(db)

    def generate(self, payload: Any, user, data_scope) -> dict[str, Any]:
        if user.role == "viewer":
            raise PermissionError("El perfil de consulta no puede generar alertas")
        request = AlertGenerateRequest.parse(payload)
        report, _ = self.sales_coach._report(
            request.fecha_desde, request.fecha_hasta, data_scope
        )
        period = request.fecha_hasta[:7]
        signals = self._commercial_signals(report, period)
        signals.extend(self._objective_signals(user, data_scope, period))
        if user.role == "admin":
            signals.extend(self._operational_signals())
        created = updated = 0
        alert_ids = []
        for signal in signals:
            signal["assignee"] = self._default_assignee(signal)
            signal["dedupe_key"] = self._dedupe_key(signal)
            alert, was_created = self.repository.upsert_signal(signal, user.id)
            alert_ids.append(alert["alert_id"])
            created += int(was_created)
            updated += int(not was_created)
        return {
            "generated": len(signals),
            "created": created,
            "updated": updated,
            "alertIds": alert_ids,
            "period": period,
        }

    def list(
        self,
        user,
        data_scope,
        *,
        status: str | None = None,
        alert_type: str | None = None,
        period: str | None = None,
    ) -> list[dict[str, Any]]:
        query = {"company_key": COMPANY_KEY, **self._visibility_query(user, data_scope)}
        if status:
            query["status"] = status
        if alert_type:
            query["type"] = alert_type
        if period:
            query["period"] = period
        return self.repository.list(query)

    def history(self, alert_id: str, user, data_scope) -> list[dict[str, Any]]:
        alert = self._authorized(alert_id, user, data_scope)
        return alert.get("history", [])

    def assign(self, payload: Any, user, data_scope) -> dict[str, Any]:
        request = AlertAssignRequest.parse(payload)
        alert = self._authorized(request.alert_id, user, data_scope, manage=True)
        if user.role == "seller":
            raise PermissionError("El vendedor no puede reasignar alertas")
        if alert["status"] in {"resolved", "dismissed"}:
            raise ValueError("No se puede asignar una alerta cerrada")
        assignee = self._resolve_assignee(request.assignee_user_id, alert, user)
        return self.repository.assign(
            request.alert_id, assignee, request.due_date, user.id, request.comment
        )

    def transition(self, payload: Any, user, data_scope) -> dict[str, Any]:
        request = AlertTransitionRequest.parse(payload)
        alert = self._authorized(request.alert_id, user, data_scope, manage=True)
        allowed = TRANSITIONS.get(alert["status"], set())
        if request.status not in allowed:
            raise ValueError(
                f"No se puede pasar una alerta de {alert['status']} a {request.status}"
            )
        if user.role == "seller" and request.status == "dismissed":
            raise PermissionError("El vendedor no puede descartar alertas")
        return self.repository.transition(
            request.alert_id,
            {alert["status"]},
            request.status,
            user.id,
            request.comment,
            request.result,
        )

    def generate_operational(self, actor_id: str = "system") -> dict[str, int]:
        created = updated = 0
        for signal in self._operational_signals():
            signal["assignee"] = self._default_assignee(signal)
            signal["dedupe_key"] = self._dedupe_key(signal)
            _, was_created = self.repository.upsert_signal(signal, actor_id)
            created += int(was_created)
            updated += int(not was_created)
        return {"created": created, "updated": updated}

    def _commercial_signals(self, report: dict[str, Any], period: str) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        sellers = report.get("dashboards", {}).get("sellers", {}).get("rows", [])
        for row in sellers:
            seller_key = str(row.get("sellerKey") or "").strip() or None
            seller_name = str(row.get("seller") or "").strip()
            if not seller_key or seller_name == "Sin vendedor":
                continue
            growth = float(row.get("growthPct") or 0)
            if growth <= -12:
                signals.append(self._signal(
                    "seller_decline", "critical" if growth <= -25 else "warning",
                    period, seller_key=seller_key, seller_name=seller_name,
                    metric="sales_growth_pct",
                    evidence={"growth_pct": growth, "sales": row.get("sales", 0), "previous_sales": row.get("previousSales", 0)},
                    message=f"{seller_name} registra una caída de {abs(growth):.1f}% frente al período comparativo.",
                ))
            concentration = float(row.get("top3ClientsSharePct") or 0)
            if concentration >= 50:
                signals.append(self._signal(
                    "high_concentration", "critical" if concentration >= 70 else "warning",
                    period, seller_key=seller_key, seller_name=seller_name,
                    metric="top3_clients_share_pct",
                    evidence={"top3_clients_share_pct": concentration},
                    message=f"El {concentration:.1f}% de la venta de {seller_name} se concentra en tres clientes.",
                ))

        clients = report.get("dashboards", {}).get("clients", {}).get("rows", [])
        for row in clients:
            status = str(row.get("status") or "")
            growth = float(row.get("growthPct") or 0)
            seller_name = str(row.get("seller") or "").strip()
            seller_key = self._seller_key(seller_name)
            common = {
                "period": period,
                "seller_key": seller_key,
                "seller_name": seller_name,
                "client_key": str(row.get("clientKey") or ""),
                "client_name": str(row.get("client") or ""),
            }
            if status in {"Dormido", "Reactivable", "Perdido"}:
                signals.append(self._signal(
                    "inactive_client", "critical" if status == "Perdido" else "warning",
                    metric="recency_days",
                    evidence={"status": status, "recency_days": row.get("recencyDays"), "previous_sales": row.get("previousSales", 0)},
                    message=f"{common['client_name']} está {status.lower()} y acumula {row.get('recencyDays', 0)} días sin compra.",
                    **common,
                ))
            if row.get("previousSales", 0) and growth <= -30:
                signals.append(self._signal(
                    "abrupt_client_decline", "critical" if growth <= -60 else "warning",
                    metric="sales_growth_pct",
                    evidence={"growth_pct": growth, "sales": row.get("sales", 0), "previous_sales": row.get("previousSales", 0)},
                    message=f"La venta de {common['client_name']} cayó {abs(growth):.1f}% frente al período comparativo.",
                    **common,
                ))
            if row.get("sales", 0) > 0 and int(row.get("families") or 0) <= 1:
                signals.append(self._signal(
                    "mix_loss", "warning", metric="active_families",
                    evidence={"active_families": int(row.get("families") or 0), "sales": row.get("sales", 0)},
                    message=f"{common['client_name']} compra una familia o menos; revisar pérdida de mix.",
                    **common,
                ))

        for source_alert in report.get("alerts", []) or []:
            if source_alert.get("category") == "Mix" and "retracción" in str(source_alert.get("title") or ""):
                category = str(source_alert.get("title") or "").replace(" en retracción", "")
                signals.append(self._signal(
                    "category_decline", "warning", period, category_key=category,
                    metric="category_growth_pct",
                    evidence={"detail": source_alert.get("detail")},
                    message=str(source_alert.get("detail") or source_alert.get("title")),
                ))
        return signals

    def _objective_signals(self, user, data_scope, period: str) -> list[dict[str, Any]]:
        signals = []
        for objective in ObjectiveService(self.db).list(
            user, data_scope, period=period, status="active"
        ):
            progress = objective.get("progress") or {}
            fulfillment = float(progress.get("fulfillment_pct") or 0)
            projection = float(progress.get("projection_value") or 0)
            target = float(objective.get("target_value") or 0)
            if target and projection < target * 0.9:
                seller_key = objective["scope_key"] if objective["scope_type"] == "seller" else None
                signals.append(self._signal(
                    "objective_at_risk", "critical" if fulfillment < 60 else "warning",
                    period, seller_key=seller_key,
                    seller_name=self._seller_name(seller_key) if seller_key else None,
                    metric=objective["metric"],
                    evidence={"fulfillment_pct": fulfillment, "projection_value": projection, "target_value": target},
                    message=f"El objetivo {objective['metric']} proyecta {projection:.2f} frente a una meta de {target:.2f}.",
                    source="objective_engine",
                    entity_key=objective["objective_id"],
                ))
        return signals

    def _operational_signals(self) -> list[dict[str, Any]]:
        latest = self.db["sync_runs"].find_one(
            {"entity": "sales"}, sort=[("finished_at", -1), ("started_at", -1)]
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        period = now.strftime("%Y-%m")
        signals = []
        threshold = int(os.getenv("ALERT_SYNC_MAX_AGE_HOURS", "30"))
        finished = latest.get("finished_at") if latest else None
        if not finished or now - finished > timedelta(hours=threshold):
            age = round((now - finished).total_seconds() / 3600, 1) if finished else None
            signals.append(self._signal(
                "sync_delayed", "critical" if age is None or age > threshold * 2 else "warning",
                period, metric="sync_age_hours",
                evidence={"age_hours": age, "threshold_hours": threshold, "last_run_id": latest.get("run_id") if latest else None},
                message="La sincronización de ventas está atrasada o no tiene una ejecución exitosa reciente.",
                source="sync_monitor",
            ))
        reconciliation = (latest or {}).get("reconciliation") or {}
        if latest and reconciliation.get("consistent") is False:
            signals.append(self._signal(
                "storage_divergence", "critical", period,
                metric="mongo_clickhouse_difference",
                evidence={"run_id": latest.get("run_id"), "reconciliation": reconciliation},
                message="La última conciliación detectó divergencia entre MongoDB y ClickHouse.",
                source="reconciliation",
            ))
        return signals

    def _authorized(self, alert_id, user, data_scope, manage=False):
        alert = self.repository.get(alert_id)
        if alert is None:
            raise LookupError("Alerta no encontrada")
        if not self._visible(alert, user, data_scope):
            raise PermissionError("La alerta está fuera de tu alcance")
        if manage and user.role not in MANAGE_ROLES:
            raise PermissionError("Tu rol no puede gestionar alertas")
        if manage and user.role == "seller":
            assignee = alert.get("assignee") or {}
            if alert.get("seller_key") != user.seller_key and assignee.get("user_id") != user.id:
                raise PermissionError("Sólo podés gestionar tus propias alertas")
        return alert

    def _visibility_query(self, user, data_scope):
        if user.role in {"admin", "commercial_director", "viewer"}:
            return {}
        if user.role == "seller":
            return {"seller_key": user.seller_key}
        seller_names = list((data_scope or {}).get("seller_name") or [])
        seller_keys = [
            row.get("seller_key")
            for row in self.db["erp_sellers"].find(
                {"seller_name": {"$in": seller_names}}, {"seller_key": 1}
            )
            if row.get("seller_key")
        ]
        return {"seller_key": {"$in": seller_keys}}

    def _visible(self, alert, user, data_scope):
        if user.role in {"admin", "commercial_director", "viewer"}:
            return True
        if user.role == "seller":
            return alert.get("seller_key") == user.seller_key
        query = self._visibility_query(user, data_scope)
        return alert.get("seller_key") in set(query.get("seller_key", {}).get("$in", []))

    def _default_assignee(self, signal):
        seller_key = signal.get("seller_key")
        if seller_key:
            user = self.db["users"].find_one(
                {"seller_key": seller_key, "role": "seller", "is_active": True}
            )
            if user:
                return self._assignee(user)
        user = self.db["users"].find_one(
            {"role": {"$in": ["commercial_director", "admin"]}, "is_active": True},
            sort=[("role", 1), ("created_at", 1)],
        )
        return self._assignee(user) if user else {
            "type": "role",
            "role": "admin",
            "user_id": None,
            "name": "Administración",
        }

    def _resolve_assignee(self, user_id, alert, actor):
        if not user_id:
            return self._default_assignee(alert)
        try:
            user = self.db["users"].find_one({"_id": ObjectId(user_id), "is_active": True})
        except Exception as exc:
            raise ValueError("assignee_user_id inválido") from exc
        if not user:
            raise ValueError("Responsable no encontrado o inactivo")
        if actor.role == "supervisor" and user.get("seller_key") != alert.get("seller_key"):
            raise PermissionError("Sólo podés asignar la alerta al vendedor involucrado")
        return self._assignee(user)

    @staticmethod
    def _assignee(user):
        return {
            "type": "user",
            "user_id": str(user["_id"]),
            "role": user.get("role"),
            "name": user.get("name") or user.get("email"),
        }

    def _seller_key(self, seller_name):
        row = self.db["erp_sellers"].find_one({"seller_name": seller_name}, {"seller_key": 1})
        return row.get("seller_key") if row else None

    def _seller_name(self, seller_key):
        row = self.db["erp_sellers"].find_one({"seller_key": seller_key}, {"seller_name": 1})
        return row.get("seller_name") if row else None

    @staticmethod
    def _signal(
        alert_type, severity, period, *, metric, evidence, message,
        seller_key=None, seller_name=None, client_key=None, client_name=None,
        category_key=None, source="commercial_rules", entity_key=None,
    ):
        return {
            "company_key": COMPANY_KEY,
            "type": alert_type,
            "severity": severity,
            "severity_rank": {"info": 1, "warning": 2, "critical": 3}[severity],
            "period": period,
            "seller_key": seller_key,
            "seller_name": seller_name,
            "client_key": client_key,
            "client_name": client_name,
            "category_key": category_key,
            "metric": metric,
            "evidence": evidence,
            "message": message,
            "source": source,
            "rule_id": alert_type.upper(),
            "rule_version": 1,
            "entity_key": entity_key,
        }

    @staticmethod
    def _dedupe_key(signal):
        identity = {
            key: signal.get(key)
            for key in (
                "company_key", "period", "type", "seller_key", "client_key",
                "category_key", "entity_key", "rule_version",
            )
        }
        return hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
