from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING


COLLECTION = "meeting_reports"


class MeetingRepository:
    def __init__(self, db):
        if db is None:
            raise RuntimeError("MongoDB no está configurado")
        self.collection = db[COLLECTION]
        self.ensure_indexes()

    def ensure_indexes(self):
        self.collection.create_index("report_id", unique=True, name="meeting_report_id_unique")
        self.collection.create_index(
            [("created_by", ASCENDING), ("created_at", DESCENDING)],
            name="meeting_creator_created",
        )
        self.collection.create_index(
            [("period.end", DESCENDING), ("report_type", ASCENDING)],
            name="meeting_period_type",
        )

    def create(self, report: dict[str, Any], created_by: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        document = {
            **report,
            "report_id": uuid4().hex,
            "version": 1,
            "created_by": created_by,
            "created_at": now,
        }
        self.collection.insert_one(document)
        return self.serialize(document)

    def get(self, report_id: str) -> dict[str, Any] | None:
        document = self.collection.find_one({"report_id": report_id})
        return self.serialize(document) if document else None

    def list(self, query: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
        return [
            self.serialize(item, include_payload=False)
            for item in self.collection.find(query).sort("created_at", DESCENDING).limit(min(max(limit, 1), 100))
        ]

    @staticmethod
    def serialize(document, include_payload=True):
        result = dict(document)
        result.pop("_id", None)
        if not include_payload:
            result.pop("payload", None)
        if isinstance(result.get("created_at"), datetime):
            result["created_at"] = result["created_at"].isoformat()
        return result
