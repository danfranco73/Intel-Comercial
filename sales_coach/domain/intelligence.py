"""Versioned, UI-independent intelligence contracts. No model/LLM dependency."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Protocol

SCHEMA_VERSION = "1.0"
MANAGEMENT_STATES = ("detected", "assigned", "in_progress", "resolved", "escalated")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def envelope(domain, period):
    return dict(schema_version=SCHEMA_VERSION, domain=domain, status="ok", generated_at=now_iso(),
                period=period, summary={}, deviations=[], drilldowns={}, explanations=[],
                recommended_actions=[], data_quality={"issues": [], "evidence": []})


def signal(domain, entity, metric, comparison, evidence_ids):
    return dict(id=fingerprint([domain, entity, metric, comparison, evidence_ids]), domain=domain,
                entity=entity, metric=metric, comparison=comparison, evidence_ids=evidence_ids,
                severity="unassessed", probable_cause=None, suggested_owner=None,
                suggested_action=None, confidence={"level": "unknown", "method": "not_calibrated"},
                analytical_status="observed", management_status="detected",
                available_management_states=list(MANAGEMENT_STATES))


class ExpectationProvider(Protocol):
    def evaluate(self, *, history, period, scope, metric, calendar): ...


class CrossDomainProvider(Protocol):
    def compatible_context(self, *, sales_context, stock_snapshot, product_identity): ...


def cross_domain_readiness(identity, *, units_verified=False, location_verified=False):
    reasons = []
    if identity.get("status") != "verified":
        reasons.append("PRODUCT_IDENTITY_UNVERIFIED")
    if not units_verified:
        reasons.append("UNITS_UNVERIFIED")
    if not location_verified:
        reasons.append("LOCATION_UNVERIFIED")
    return {"allowed": not reasons, "blocking_reasons": reasons, "heuristics_enabled": False}
