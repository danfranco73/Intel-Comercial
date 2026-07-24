from __future__ import annotations

from typing import Any


OPERATORS = {
    "eq": lambda left, right: left == right,
    "lt": lambda left, right: left < right,
    "lte": lambda left, right: left <= right,
    "gt": lambda left, right: left > right,
    "gte": lambda left, right: left >= right,
}


class CoachCommentEngine:
    def __init__(self, rules: list[dict[str, Any]]):
        self.rules = sorted(
            [rule for rule in rules if rule.get("enabled", True)],
            key=lambda item: int(item.get("priority", 0)),
            reverse=True,
        )

    def evaluate(
        self,
        metrics: dict[str, Any],
        context: dict[str, Any] | None = None,
        *,
        max_strengths: int = 3,
        max_opportunities: int = 3,
    ) -> dict[str, Any]:
        values = {**(context or {}), **metrics}
        matches = []
        for rule in self.rules:
            if self._matches(rule.get("condition") or {}, values):
                comment = self._render(rule, values)
                if comment is not None:
                    matches.append(comment)
        selected = self._resolve_conflicts(matches)
        strengths = [
            item for item in selected if item["category"] == "strength"
        ][:max_strengths]
        opportunities = [
            item for item in selected if item["category"] == "opportunity"
        ][:max_opportunities]
        return {
            "strengths": strengths,
            "opportunities": opportunities,
            "actionPlan": [
                {
                    "priority": index + 1,
                    "action": item["action"],
                    "evidence": item["evidence"],
                    "rule_id": item["rule_id"],
                    "rule_version": item["rule_version"],
                }
                for index, item in enumerate(opportunities)
            ],
            "evaluatedRuleCount": len(self.rules),
            "matchedRuleCount": len(matches),
        }

    @staticmethod
    def _matches(condition: dict[str, Any], values: dict[str, Any]) -> bool:
        metric = condition.get("metric")
        if not metric or values.get(metric) is None:
            return False
        operator = str(condition.get("operator") or "")
        left = values[metric]
        if operator.endswith("_context"):
            base_operator = operator.removesuffix("_context")
            context_key = condition.get("context")
            if not context_key or values.get(context_key) is None:
                return False
            right = values[context_key]
            operator = base_operator
        else:
            right = condition.get("value")
        comparator = OPERATORS.get(operator)
        if comparator is None:
            return False
        try:
            return bool(comparator(left, right))
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _render(rule: dict[str, Any], values: dict[str, Any]) -> dict[str, Any] | None:
        evidence_fields = list(rule.get("evidence_fields") or [])
        if not evidence_fields or any(values.get(field) is None for field in evidence_fields):
            return None
        evidence = {field: values[field] for field in evidence_fields}
        try:
            message = str(rule["message_template"]).format(**values)
            action = str(rule["action_template"]).format(**values)
        except (KeyError, ValueError):
            return None
        return {
            "rule_id": rule["rule_id"],
            "rule_version": int(rule.get("version", 1)),
            "priority": int(rule.get("priority", 0)),
            "category": rule["category"],
            "message": message,
            "evidence": evidence,
            "action": action,
            "conflict_group": rule.get("conflict_group"),
        }

    @staticmethod
    def _resolve_conflicts(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected = []
        occupied: set[str] = set()
        seen_rules: set[str] = set()
        for item in sorted(matches, key=lambda value: value["priority"], reverse=True):
            rule_id = item["rule_id"]
            group = str(item.get("conflict_group") or "")
            if rule_id in seen_rules or (group and group in occupied):
                continue
            seen_rules.add(rule_id)
            if group:
                occupied.add(group)
            selected.append(item)
        return selected
