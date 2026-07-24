from __future__ import annotations

from typing import Any

class PreferencesRepository:
    def __init__(self, db: Any):
        self.db = db

    def load(self, user_id: str) -> dict[str, Any] | None:
        return self.db["sessions"].find_one(
            {"_id": user_id},
            {"_id": 0, "datasets": 1, "planning": 1},
        )

    def save(self, user_id: str, datasets: dict[str, Any], planning: Any = None) -> bool:
        payload = {"datasets": datasets}
        if planning is not None:
            payload["planning"] = planning
        self.db["sessions"].update_one(
            {"_id": user_id},
            {"$set": payload},
            upsert=True,
        )
        return True
