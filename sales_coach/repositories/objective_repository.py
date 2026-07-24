from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from bson.decimal128 import Decimal128
from pymongo import ASCENDING, DESCENDING, ReturnDocument


OBJECTIVES_COLLECTION = "commercial_objectives"


class ObjectiveRepository:
    def __init__(self, db):
        if db is None:
            raise RuntimeError("MongoDB no está configurado")
        self.db = db
        self.collection = db[OBJECTIVES_COLLECTION]
        self.ensure_indexes()

    def ensure_indexes(self) -> None:
        self.collection.create_index(
            [("objective_id", ASCENDING), ("version", ASCENDING)],
            unique=True,
            name="objective_version_unique",
        )
        self.collection.create_index(
            [
                ("company_key", ASCENDING),
                ("period", DESCENDING),
                ("scope_type", ASCENDING),
                ("scope_key", ASCENDING),
                ("metric", ASCENDING),
                ("current", ASCENDING),
            ],
            name="objective_lookup",
        )
        self.collection.create_index(
            [
                ("company_key", ASCENDING),
                ("period", ASCENDING),
                ("scope_type", ASCENDING),
                ("scope_key", ASCENDING),
                ("metric", ASCENDING),
            ],
            unique=True,
            partialFilterExpression={"current": True},
            name="objective_current_unique",
        )
        self.collection.create_index(
            [("status", ASCENDING), ("period", DESCENDING)],
            name="objective_status_period",
        )
        self.collection.create_index(
            [("migration_key", ASCENDING)],
            unique=True,
            sparse=True,
            name="objective_migration_unique",
        )

    def create(
        self,
        data: dict[str, Any],
        created_by: str,
        *,
        status: str = "draft",
        migration_key: str | None = None,
    ) -> dict[str, Any]:
        now = self._utc_now()
        document = {
            "objective_id": uuid4().hex,
            "version": 1,
            "current": True,
            "company_key": data["company_key"],
            "period": data["period"],
            "scope_type": data["scope_type"],
            "scope_key": data["scope_key"],
            "metric": data["metric"],
            "target_value": Decimal128(data["target_value"]),
            "baseline_value": (
                Decimal128(data["baseline_value"])
                if data.get("baseline_value") is not None
                else None
            ),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "status": status,
            "notes": data.get("notes"),
            "approved_by": created_by if status == "active" else None,
            "approved_at": now if status == "active" else None,
            "closed_by": None,
            "closed_at": None,
        }
        if migration_key:
            document["migration_key"] = migration_key
        self.collection.insert_one(document)
        return self.serialize(document)

    def get_current(self, objective_id: str) -> dict[str, Any] | None:
        return self.collection.find_one({"objective_id": objective_id, "current": True})

    def revise(
        self,
        objective_id: str,
        changes: dict[str, Any],
        changed_by: str,
    ) -> dict[str, Any]:
        current = self.get_current(objective_id)
        if current is None:
            raise LookupError("Objetivo no encontrado")
        if current["status"] != "draft":
            raise ValueError("Sólo se pueden editar objetivos en borrador")
        next_document = dict(current)
        next_document.pop("_id", None)
        next_document["version"] = int(current["version"]) + 1
        next_document["current"] = True
        next_document["updated_at"] = self._utc_now()
        next_document["created_by"] = changed_by
        next_document["created_at"] = self._utc_now()
        if changes.get("target_value") is not None:
            next_document["target_value"] = Decimal128(changes["target_value"])
        if changes.get("baseline_provided"):
            next_document["baseline_value"] = (
                Decimal128(changes["baseline_value"])
                if changes.get("baseline_value") is not None
                else None
            )
        if changes.get("notes_provided"):
            next_document["notes"] = changes.get("notes")
        self.collection.update_one({"_id": current["_id"]}, {"$set": {"current": False}})
        try:
            self.collection.insert_one(next_document)
        except Exception:
            self.collection.update_one({"_id": current["_id"]}, {"$set": {"current": True}})
            raise
        return self.serialize(next_document)

    def transition(
        self,
        objective_id: str,
        from_status: str,
        to_status: str,
        actor_id: str,
    ) -> dict[str, Any]:
        now = self._utc_now()
        transition_fields: dict[str, Any] = {
            "status": to_status,
            "updated_at": now,
        }
        if to_status == "active":
            transition_fields.update({"approved_by": actor_id, "approved_at": now})
        if to_status == "closed":
            transition_fields.update({"closed_by": actor_id, "closed_at": now})
        document = self.collection.find_one_and_update(
            {
                "objective_id": objective_id,
                "current": True,
                "status": from_status,
            },
            {"$set": transition_fields},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ValueError(f"El objetivo no está en estado {from_status}")
        return self.serialize(document)

    def list_current(self, query: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
        current_query = {**query, "current": True}
        cursor = self.collection.find(current_query).sort(
            [("period", DESCENDING), ("scope_type", ASCENDING), ("scope_key", ASCENDING)]
        ).limit(min(max(limit, 1), 500))
        return [self.serialize(item) for item in cursor]

    def history(self, objective_id: str) -> list[dict[str, Any]]:
        return [
            self.serialize(item)
            for item in self.collection.find({"objective_id": objective_id}).sort(
                "version", DESCENDING
            )
        ]

    @staticmethod
    def serialize(document: dict[str, Any]) -> dict[str, Any]:
        result = dict(document)
        result.pop("_id", None)
        for field in ("target_value", "baseline_value"):
            if isinstance(result.get(field), Decimal128):
                result[field] = str(result[field].to_decimal())
            elif isinstance(result.get(field), Decimal):
                result[field] = str(result[field])
        for field in ("created_at", "updated_at", "approved_at", "closed_at"):
            if isinstance(result.get(field), datetime):
                result[field] = result[field].isoformat()
        return result

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)
