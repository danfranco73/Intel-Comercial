from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class AnalysisRepository:
    def __init__(self, db: Any):
        self.db = db

    def record(self, filters: dict[str, Any], result: dict[str, Any]) -> None:
        self.db["registros"].insert_one(
            {
                "timestamp": datetime.now(timezone.utc),
                "filters": filters,
                "meta": result.get("meta", {}),
                "summary": result.get("summary", {}),
                "insightsSummary": result.get("insightsSummary"),
            }
        )

    def list_recent(self, limit: int) -> list[dict[str, Any]]:
        cursor = (
            self.db["registros"]
            .find({}, {"_id": 0})
            .sort("timestamp", -1)
            .limit(min(max(limit, 1), 200))
        )
        return list(cursor)
