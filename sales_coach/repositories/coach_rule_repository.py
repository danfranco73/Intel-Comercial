from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING

from sales_coach.domain.default_coach_rules import DEFAULT_SELLER_RULES


RULE_SETS_COLLECTION = "coach_rule_sets"
COMMENT_AUDITS_COLLECTION = "coach_comment_audits"


class CoachRuleRepository:
    def __init__(self, db):
        if db is None:
            raise RuntimeError("MongoDB no está configurado")
        self.db = db
        self.rules = db[RULE_SETS_COLLECTION]
        self.audits = db[COMMENT_AUDITS_COLLECTION]
        self.ensure_indexes()

    def ensure_indexes(self) -> None:
        self.rules.create_index(
            [("rule_set_id", ASCENDING), ("version", ASCENDING)],
            unique=True,
            name="coach_rule_set_version_unique",
        )
        self.rules.create_index(
            [("entity_type", ASCENDING), ("active", ASCENDING), ("version", DESCENDING)],
            name="coach_rule_set_active",
        )
        self.audits.create_index(
            [("audit_id", ASCENDING)], unique=True, name="coach_audit_id_unique"
        )
        self.audits.create_index(
            [("entity_type", ASCENDING), ("entity_key", ASCENDING), ("created_at", DESCENDING)],
            name="coach_audit_entity_created",
        )
        self.audits.create_index(
            [("user_id", ASCENDING), ("created_at", DESCENDING)],
            name="coach_audit_user_created",
        )

    def seed_defaults(self) -> dict[str, Any]:
        existing = self.rules.find_one({"rule_set_id": "seller_comments", "version": 1})
        if existing:
            return self._serialize(existing)
        now = self._utc_now()
        document = {
            "rule_set_id": "seller_comments",
            "entity_type": "seller",
            "version": 1,
            "active": True,
            "rules": DEFAULT_SELLER_RULES,
            "created_by": "phase6_migration",
            "created_at": now,
            "notes": "Reglas deterministas iniciales de Codenoa Sales Coach.",
        }
        self.rules.insert_one(document)
        return self._serialize(document)

    def active_rules(self, entity_type: str = "seller") -> dict[str, Any]:
        document = self.rules.find_one(
            {"entity_type": entity_type, "active": True},
            sort=[("version", DESCENDING)],
        )
        if document is None and entity_type == "seller":
            return {
                "rule_set_id": "seller_comments",
                "entity_type": "seller",
                "version": 1,
                "active": True,
                "rules": DEFAULT_SELLER_RULES,
                "source": "code_fallback",
            }
        return self._serialize(document) if document else {
            "entity_type": entity_type,
            "rules": [],
            "source": "empty",
        }

    def create_version(
        self,
        entity_type: str,
        rules: list[dict[str, Any]],
        actor_id: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        self._validate_rules(rules)
        current = self.rules.find_one(
            {"entity_type": entity_type}, sort=[("version", DESCENDING)]
        )
        version = int(current.get("version", 0) if current else 0) + 1
        rule_set_id = str(current.get("rule_set_id") if current else f"{entity_type}_comments")
        now = self._utc_now()
        document = {
            "rule_set_id": rule_set_id,
            "entity_type": entity_type,
            "version": version,
            "active": True,
            "rules": rules,
            "created_by": actor_id,
            "created_at": now,
            "notes": notes,
        }
        self.rules.update_many(
            {"entity_type": entity_type, "active": True},
            {"$set": {"active": False, "deactivated_at": now}},
        )
        try:
            self.rules.insert_one(document)
        except Exception:
            if current:
                self.rules.update_one(
                    {"_id": current["_id"]}, {"$set": {"active": True}, "$unset": {"deactivated_at": ""}}
                )
            raise
        return self._serialize(document)

    def audit(
        self,
        *,
        user_id: str,
        entity_type: str,
        entity_key: str,
        date_range: dict[str, str],
        rule_set: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        audit_id = uuid4().hex
        self.audits.insert_one(
            {
                "audit_id": audit_id,
                "user_id": user_id,
                "entity_type": entity_type,
                "entity_key": entity_key,
                "range": date_range,
                "rule_set_id": rule_set.get("rule_set_id"),
                "rule_set_version": rule_set.get("version"),
                "strengths": result.get("strengths") or [],
                "opportunities": result.get("opportunities") or [],
                "created_at": self._utc_now(),
            }
        )
        return audit_id

    def list_audits(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            self._serialize(item)
            for item in self.audits.find({}, {"_id": 0})
            .sort("created_at", DESCENDING)
            .limit(min(max(limit, 1), 100))
        ]

    @staticmethod
    def _validate_rules(rules: list[dict[str, Any]]) -> None:
        if not isinstance(rules, list) or not rules:
            raise ValueError("rules debe ser una lista no vacía")
        ids = set()
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError("Cada regla debe ser un objeto")
            rule_id = str(rule.get("rule_id") or "").strip()
            if not rule_id or rule_id in ids:
                raise ValueError("rule_id es obligatorio y no puede repetirse")
            ids.add(rule_id)
            if rule.get("category") not in {"strength", "opportunity"}:
                raise ValueError(f"Categoría inválida en {rule_id}")
            if not isinstance(rule.get("condition"), dict):
                raise ValueError(f"Condición inválida en {rule_id}")
            if not rule.get("message_template") or not rule.get("action_template"):
                raise ValueError(f"Faltan plantillas en {rule_id}")
            if not rule.get("evidence_fields"):
                raise ValueError(f"Falta evidencia en {rule_id}")

    @staticmethod
    def _serialize(document: dict[str, Any]) -> dict[str, Any]:
        result = dict(document)
        result.pop("_id", None)
        for field in ("created_at", "deactivated_at"):
            if isinstance(result.get(field), datetime):
                result[field] = result[field].isoformat()
        return result

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)
