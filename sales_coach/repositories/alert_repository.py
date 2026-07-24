from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument


ALERTS_COLLECTION = "commercial_alerts"


class AlertRepository:
    def __init__(self, db):
        if db is None:
            raise RuntimeError("MongoDB no está configurado")
        self.collection = db[ALERTS_COLLECTION]
        self.ensure_indexes()

    def ensure_indexes(self) -> None:
        self.collection.create_index("dedupe_key", unique=True, name="alert_dedupe_unique")
        self.collection.create_index(
            [("company_key", ASCENDING), ("status", ASCENDING), ("period", DESCENDING)],
            name="alert_status_period",
        )
        self.collection.create_index(
            [("seller_key", ASCENDING), ("created_at", DESCENDING)],
            name="alert_seller_created",
        )
        self.collection.create_index(
            [("assignee.user_id", ASCENDING), ("status", ASCENDING)],
            name="alert_assignee_status",
        )
        self.collection.create_index("due_date", name="alert_due_date")

    def upsert_signal(self, signal: dict[str, Any], actor_id: str) -> tuple[dict[str, Any], bool]:
        now = self._utc_now()
        existed = self.collection.find_one(
            {"dedupe_key": signal["dedupe_key"]}, {"_id": 1}
        ) is not None
        refresh_fields = {
            key: value for key, value in signal.items() if key != "assignee"
        }
        initial_event = {
            "event_id": uuid4().hex,
            "type": "created",
            "from_status": None,
            "to_status": "assigned" if signal.get("assignee") else "new",
            "actor_id": actor_id,
            "comment": "Alerta generada automáticamente",
            "result": None,
            "created_at": now,
        }
        inserted = self.collection.find_one_and_update(
            {"dedupe_key": signal["dedupe_key"]},
            {
                "$set": {
                    **refresh_fields,
                    "last_seen_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "alert_id": uuid4().hex,
                    "status": "assigned" if signal.get("assignee") else "new",
                    "created_at": now,
                    "resolved_at": None,
                    "comment": None,
                    "result": None,
                    "history": [initial_event],
                    "assignee": signal.get("assignee"),
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return self.serialize(inserted), not existed

    def get(self, alert_id: str) -> dict[str, Any] | None:
        return self.collection.find_one({"alert_id": alert_id})

    def list(self, query: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
        self.mark_overdue(query)
        cursor = self.collection.find(query).sort(
            [("severity_rank", DESCENDING), ("created_at", DESCENDING)]
        ).limit(min(max(limit, 1), 500))
        return [self.serialize(item) for item in cursor]

    def assign(
        self,
        alert_id: str,
        assignee: dict[str, Any],
        due_date: str | None,
        actor_id: str,
        comment: str | None,
    ) -> dict[str, Any]:
        current = self.get(alert_id)
        if current is None:
            raise LookupError("Alerta no encontrada")
        now = self._utc_now()
        event = self._event("assigned", current["status"], "assigned", actor_id, comment, None)
        document = self.collection.find_one_and_update(
            {"alert_id": alert_id, "updated_at": current["updated_at"]},
            {
                "$set": {
                    "assignee": assignee,
                    "due_date": due_date,
                    "status": "assigned",
                    "updated_at": now,
                },
                "$push": {"history": event},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ValueError("La alerta fue modificada; recargá e intentá nuevamente")
        return self.serialize(document)

    def transition(
        self,
        alert_id: str,
        allowed_from: set[str],
        to_status: str,
        actor_id: str,
        comment: str | None,
        result: str | None,
    ) -> dict[str, Any]:
        current = self.get(alert_id)
        if current is None:
            raise LookupError("Alerta no encontrada")
        if current.get("status") not in allowed_from:
            raise ValueError("Transición de estado no permitida")
        now = self._utc_now()
        event = self._event(
            "status_changed", current.get("status"), to_status, actor_id, comment, result
        )
        set_fields: dict[str, Any] = {
            "status": to_status,
            "updated_at": now,
            "comment": comment,
        }
        if result is not None:
            set_fields["result"] = result
        if to_status in {"resolved", "dismissed"}:
            set_fields["resolved_at"] = now
        document = self.collection.find_one_and_update(
            {"alert_id": alert_id, "status": {"$in": list(allowed_from)}},
            {"$set": set_fields, "$push": {"history": event}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ValueError("Transición de estado no permitida")
        return self.serialize(document)

    def mark_overdue(self, base_query: dict[str, Any] | None = None) -> None:
        today = date.today().isoformat()
        query = {
            **(base_query or {}),
            "due_date": {"$lt": today},
            "status": {"$in": ["new", "assigned", "in_progress"]},
        }
        for alert in self.collection.find(query, {"alert_id": 1, "status": 1}):
            now = self._utc_now()
            self.collection.update_one(
                {"alert_id": alert["alert_id"], "status": alert["status"]},
                {
                    "$set": {"status": "overdue", "updated_at": now},
                    "$push": {
                        "history": self._event(
                            "status_changed",
                            alert["status"],
                            "overdue",
                            "system",
                            "Vencimiento automático por fecha límite",
                            None,
                        )
                    },
                },
            )

    @staticmethod
    def serialize(document: dict[str, Any] | None) -> dict[str, Any]:
        if not document:
            return {}
        result = dict(document)
        result.pop("_id", None)
        for field in ("created_at", "updated_at", "last_seen_at", "resolved_at"):
            if isinstance(result.get(field), datetime):
                result[field] = result[field].isoformat()
        result["history"] = [
            {
                **event,
                "created_at": (
                    event["created_at"].isoformat()
                    if isinstance(event.get("created_at"), datetime)
                    else event.get("created_at")
                ),
            }
            for event in result.get("history", [])
        ]
        return result

    @classmethod
    def _event(cls, event_type, from_status, to_status, actor_id, comment, result):
        return {
            "event_id": uuid4().hex,
            "type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "actor_id": actor_id,
            "comment": comment,
            "result": result,
            "created_at": cls._utc_now(),
        }

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)
