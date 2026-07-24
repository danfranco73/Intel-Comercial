from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError


SYNC_RUNS_COLLECTION = "sync_runs"
SYNC_CHECKPOINTS_COLLECTION = "sync_checkpoints"
SCHEDULER_LEASES_COLLECTION = "scheduler_leases"


class SyncRepository:
    def __init__(self, db):
        if db is None:
            raise RuntimeError("MongoDB no está configurado")
        self.db = db
        self.ensure_indexes()

    def ensure_indexes(self) -> None:
        self.db[SYNC_RUNS_COLLECTION].create_index(
            [("run_id", ASCENDING)], unique=True, name="sync_run_id_unique"
        )
        self.db[SYNC_RUNS_COLLECTION].create_index(
            [("entity", ASCENDING), ("started_at", DESCENDING)],
            name="sync_entity_started",
        )
        self.db[SYNC_RUNS_COLLECTION].create_index(
            [("status", ASCENDING), ("started_at", DESCENDING)],
            name="sync_status_started",
        )
        self.db[SYNC_CHECKPOINTS_COLLECTION].create_index(
            [("job_key", ASCENDING)], unique=True, name="sync_checkpoint_job_unique"
        )
        self.db[SCHEDULER_LEASES_COLLECTION].create_index(
            [("expires_at", ASCENDING)], name="scheduler_lease_expiry"
        )

    def start(
        self,
        entity: str,
        source: str,
        destinations: list[str],
        date_range: dict[str, str] | None,
        origin: str,
        requested_by: str | None = None,
    ) -> str:
        run_id = uuid4().hex
        now = self._utc_now()
        self.db[SYNC_RUNS_COLLECTION].insert_one(
            {
                "run_id": run_id,
                "entity": entity,
                "source": source,
                "destinations": destinations,
                "range": date_range,
                "origin": origin,
                "requested_by": requested_by,
                "status": "running",
                "started_at": now,
                "updated_at": now,
                "finished_at": None,
                "duration_ms": None,
                "rows_read": 0,
                "rows_stored": {},
                "error": None,
                "reconciliation": None,
            }
        )
        return run_id

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        rows_read: int = 0,
        rows_stored: dict[str, int] | None = None,
        error: str | None = None,
        reconciliation: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = self._utc_now()
        existing = self.db[SYNC_RUNS_COLLECTION].find_one({"run_id": run_id})
        started_at = existing.get("started_at") if existing else now
        duration_ms = max(0, int((now - started_at).total_seconds() * 1000))
        return self.db[SYNC_RUNS_COLLECTION].find_one_and_update(
            {"run_id": run_id},
            {
                "$set": {
                    "status": status,
                    "updated_at": now,
                    "finished_at": now,
                    "duration_ms": duration_ms,
                    "rows_read": int(rows_read or 0),
                    "rows_stored": rows_stored or {},
                    "error": error,
                    "reconciliation": reconciliation,
                    "details": details or {},
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    def save_checkpoint(
        self,
        job_key: str,
        *,
        status: str,
        run_id: str | None,
        date_range: dict[str, str] | None,
        error: str | None = None,
    ) -> None:
        self.db[SYNC_CHECKPOINTS_COLLECTION].update_one(
            {"job_key": job_key},
            {
                "$set": {
                    "status": status,
                    "run_id": run_id,
                    "range": date_range,
                    "error": error,
                    "updated_at": self._utc_now(),
                }
            },
            upsert=True,
        )

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = self.db[SYNC_RUNS_COLLECTION].find(
            {},
            {"_id": 0},
        ).sort("started_at", DESCENDING).limit(min(max(limit, 1), 100))
        return [self._serialize(item) for item in cursor]

    def latest_by_entity(self) -> list[dict[str, Any]]:
        pipeline = [
            {"$sort": {"started_at": -1}},
            {"$group": {"_id": "$entity", "run": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$run"}},
            {"$project": {"_id": 0}},
        ]
        return [self._serialize(item) for item in self.db[SYNC_RUNS_COLLECTION].aggregate(pipeline)]

    def acquire_lease(self, lease_key: str, owner: str, ttl_seconds: int) -> bool:
        now = self._utc_now()
        expires_at = now + timedelta(seconds=max(ttl_seconds, 30))
        try:
            result = self.db[SCHEDULER_LEASES_COLLECTION].find_one_and_update(
                {
                    "_id": lease_key,
                    "$or": [
                        {"expires_at": {"$lte": now}},
                        {"owner": owner},
                        {"expires_at": {"$exists": False}},
                    ],
                },
                {"$set": {"owner": owner, "acquired_at": now, "expires_at": expires_at}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return bool(result and result.get("owner") == owner)

    def release_lease(self, lease_key: str, owner: str) -> None:
        self.db[SCHEDULER_LEASES_COLLECTION].delete_one({"_id": lease_key, "owner": owner})

    @staticmethod
    def _serialize(document: dict[str, Any]) -> dict[str, Any]:
        result = dict(document)
        result.pop("_id", None)
        for field in ("started_at", "updated_at", "finished_at"):
            if isinstance(result.get(field), datetime):
                result[field] = result[field].isoformat()
        return result

    @staticmethod
    def _utc_now() -> datetime:
        # PyMongo stores BSON datetimes as naive UTC unless tz_aware=True.
        return datetime.now(timezone.utc).replace(tzinfo=None)
