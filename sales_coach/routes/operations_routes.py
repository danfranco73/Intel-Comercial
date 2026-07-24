from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs

from clickhouse_client import get_clickhouse_storage_status
from erp_client import get_erp_status
from mongo_client import get_erp_storage_status
from sales_coach.repositories.sync_repository import (
    SYNC_CHECKPOINTS_COLLECTION,
    SyncRepository,
)
from sales_coach.services.reconciliation_service import ReconciliationService


class OperationsRoutesMixin:
    def handle_sync_runs(self, parsed):
        db = self.database()
        if db is None:
            self.send_json({"error": "MongoDB no está configurado"}, status=503)
            return
        query = parse_qs(parsed.query)
        try:
            limit = int(query.get("limit", ["50"])[0])
        except (TypeError, ValueError):
            limit = 50
        self.send_json({"runs": SyncRepository(db).list_recent(limit)})

    def handle_sync_reconciliation(self, parsed):
        query = parse_qs(parsed.query)
        start = str(query.get("fechaDesde", [""])[0]).strip()
        end = str(query.get("fechaHasta", [""])[0]).strip()
        if not start or not end:
            self.send_json({"error": "Faltan fechaDesde o fechaHasta"}, status=400)
            return
        try:
            result = ReconciliationService(self.database()).reconcile_sales(start, end)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json(result)

    def handle_data_freshness(self):
        mongo = get_erp_storage_status()
        clickhouse = get_clickhouse_storage_status()
        candidates = [
            value
            for value in (mongo.get("lastSyncAt"), clickhouse.get("lastSyncAt"))
            if value
        ]
        self.send_json(
            {
                "updatedAt": max(candidates) if candidates else None,
                "periodStart": clickhouse.get("periodStart") or mongo.get("periodStart"),
                "periodEnd": clickhouse.get("periodEnd") or mongo.get("periodEnd"),
                "source": "clickhouse" if clickhouse.get("available") else "mongo",
                "available": bool(clickhouse.get("available") or mongo.get("available")),
            }
        )

    def handle_health(self):
        db = self.database()
        mongo_connected = False
        if db is not None:
            try:
                db.command("ping")
                mongo_connected = True
            except Exception:
                mongo_connected = False
        clickhouse = get_clickhouse_storage_status()
        chess = get_erp_status()
        scheduler = None
        if db is not None:
            checkpoint = db[SYNC_CHECKPOINTS_COLLECTION].find_one(
                {"job_key": "sales"}, {"_id": 0}
            )
            if checkpoint:
                scheduler = dict(checkpoint)
                if isinstance(scheduler.get("updated_at"), datetime):
                    scheduler["updated_at"] = scheduler["updated_at"].isoformat()
        components = {
            "application": {"healthy": True},
            "mongodb": {"healthy": mongo_connected},
            "clickhouse": {
                "healthy": bool(clickhouse.get("connected")),
                "configured": bool(clickhouse.get("configured")),
                "pendingMutations": clickhouse.get("pendingMutations"),
            },
            "chess": {
                "healthy": bool(chess.get("reachable")),
                "configured": bool(chess.get("configured")),
            },
            "scheduler": {
                "healthy": bool(scheduler and scheduler.get("status") in {"success", "warning"}),
                "checkpoint": scheduler,
            },
        }
        healthy = components["mongodb"]["healthy"] and all(
            component["healthy"]
            for key, component in components.items()
            if key not in {"mongodb", "scheduler"}
            and (key != "clickhouse" or component.get("configured"))
        )
        self.send_json(
            {
                "status": "ok" if healthy else "degraded",
                "checkedAt": datetime.now(timezone.utc).isoformat(),
                "components": components,
            },
            status=200,
        )
