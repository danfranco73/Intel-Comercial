from __future__ import annotations

from urllib.parse import parse_qs

from sales_coach.repositories import CoachRuleRepository


class CoachRuleRoutesMixin:
    def handle_coach_rules(self):
        repository = CoachRuleRepository(self.database())
        self.send_json({"ruleSet": repository.active_rules("seller")})

    def handle_create_coach_rule_version(self):
        try:
            payload = self._read_json_body()
            if not isinstance(payload, dict):
                raise ValueError("El payload debe ser un objeto JSON")
            entity_type = str(payload.get("entity_type") or "seller").strip()
            if entity_type != "seller":
                raise ValueError("Sólo seller está habilitado en esta fase")
            result = CoachRuleRepository(self.database()).create_version(
                entity_type,
                payload.get("rules"),
                self.auth_context.user.id,
                str(payload.get("notes") or "").strip() or None,
            )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        except Exception as exc:
            self.log_application_error("coach_rule_version", exc)
            self.send_json({"error": "No se pudo crear la versión de reglas"}, status=500)
            return
        self.send_json({"ruleSet": result}, status=201)

    def handle_coach_comment_audits(self, parsed):
        query = parse_qs(parsed.query)
        try:
            limit = int(query.get("limit", ["50"])[0])
        except (TypeError, ValueError):
            limit = 50
        audits = CoachRuleRepository(self.database()).list_audits(limit)
        self.send_json({"audits": audits})
