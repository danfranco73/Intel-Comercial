"""Pure, versioned SALE -> DEPOSIT simulation. Never writes historical facts."""
from datetime import date
from sales_coach.domain.intelligence import fingerprint

SELECTORS = ("billing_company", "sales_force", "route", "channel")
REQUIRED = {"rule_id", "version", "priority", "valid_from", "valid_to", *SELECTORS,
            "product_scope", "deposit_id", "business_operation", "verified", "verified_by", "notes",
            "simulation_enabled"}


def validate_rules(rules):
    seen = set()
    for rule in rules:
        if REQUIRED - rule.keys():
            raise ValueError("Incomplete attribution rule")
        identity = (rule["rule_id"], rule["version"])
        if identity in seen or not rule["rule_id"] or not isinstance(rule["version"], int) or rule["version"] < 1:
            raise ValueError("Rule identity must be unique and version positive")
        seen.add(identity)
        if not isinstance(rule["priority"], int) or not isinstance(rule["verified"], bool):
            raise ValueError("Invalid priority or verification")
        if not isinstance(rule["simulation_enabled"], bool):
            raise ValueError("simulation_enabled must be explicit")
        if rule["verified"] and not rule["verified_by"]:
            raise ValueError("Verification needs an author")
        if not str(rule["deposit_id"]).isdigit():
            raise ValueError("Invalid deposit identifier")
        for field in SELECTORS:
            values = rule[field]
            if values is not None and (not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values)):
                raise ValueError("Selectors require nonempty explicit lists or null")
        if not isinstance(rule["product_scope"], dict) or any(k not in {"product", "family", "line", "supplier", "storage_class"}
                or not isinstance(v, list) or not v or any(not isinstance(x, str) or not x for x in v)
                for k, v in rule["product_scope"].items()):
            raise ValueError("Invalid product scope")
        if rule["route"] and not any(rule[k] for k in ("billing_company", "sales_force", "channel")):
            raise ValueError("Route number alone cannot attribute a sale")
        for key in ("valid_from", "valid_to"):
            if rule[key] is not None:
                date.fromisoformat(rule[key])
        if rule["valid_from"] and rule["valid_to"] and rule["valid_from"] > rule["valid_to"]:
            raise ValueError("Invalid effective interval")
    return fingerprint(rules)


def match(rule, fact):
    if not rule["simulation_enabled"] or not rule["verified"]:
        return "INACTIVE", []
    when = fact.get("date")
    if isinstance(when, date):
        when = when.isoformat()
    if when:
        when = date.fromisoformat(when).isoformat()
    missing = [] if when else ["date"]
    if when and ((rule["valid_from"] and when < rule["valid_from"]) or (rule["valid_to"] and when > rule["valid_to"])):
        return "NO_MATCH", []
    for key, values in [(k, rule[k]) for k in SELECTORS] + list(rule["product_scope"].items()):
        if values is None:
            continue
        actual = fact.get(key)
        if actual is None or actual == "":
            missing.append(key)
        elif str(actual) not in values:
            return "NO_MATCH", []
    return ("INSUFFICIENT" if missing else "MATCH"), missing


class DepositAttributionEngine:
    def __init__(self, rules):
        from copy import deepcopy
        self.rules = deepcopy(rules)
        self.ruleset_hash = validate_rules(self.rules)

    def evaluate(self, fact):
        matched, insufficient = [], []
        for rule in self.rules:
            status, missing = match(rule, fact)
            if status == "MATCH":
                matched.append(rule)
            elif status == "INSUFFICIENT":
                insufficient.append((rule, missing))
        result = {"status": "UNATTRIBUTED", "deposit_id": None, "business_operation": None,
            "ruleset_hash": self.ruleset_hash, "matched_rules": [], "shadowed_rules": [],
            "blocking_rules": [], "missing_fields": [], "reason": "NO_CONFIRMED_MATCH"}
        reference = lambda r: {"rule_id": r["rule_id"], "version": r["version"], "priority": r["priority"]}
        if not matched:
            result["blocking_rules"] = [reference(r) for r, _ in insufficient]
            result["missing_fields"] = sorted({f for _, fields in insufficient for f in fields})
            if insufficient:
                result["reason"] = "INSUFFICIENT_EVIDENCE"
            return result
        priority = max(r["priority"] for r in matched)
        winners = [r for r in matched if r["priority"] == priority]
        result["matched_rules"] = [reference(r) for r in winners]
        result["shadowed_rules"] = [reference(r) for r in matched if r["priority"] < priority]
        effects = {(r["deposit_id"], r["business_operation"]) for r in winners}
        if len(effects) != 1:
            result.update(status="CONFLICT", reason="COMPETING_RULES_AT_SAME_PRIORITY")
            return result
        target = next(iter(effects))
        blockers = [(r, fields) for r, fields in insufficient if r["priority"] >= priority
                    and (r["deposit_id"], r["business_operation"]) != target]
        if blockers:
            result.update(reason="UNRESOLVED_SPECIFIC_RULE", blocking_rules=[reference(r) for r, _ in blockers],
                          missing_fields=sorted({f for _, fields in blockers for f in fields}))
            return result
        result.update(status="ATTRIBUTED", deposit_id=target[0], business_operation=target[1], reason="UNIQUE_HIGHEST_PRIORITY_MATCH")
        return result
