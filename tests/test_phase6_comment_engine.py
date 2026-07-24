from __future__ import annotations

import mongomock

from sales_coach.domain import CoachCommentEngine, DEFAULT_SELLER_RULES
from sales_coach.repositories import CoachRuleRepository


def base_metrics(**overrides):
    metrics = {
        "rankSales": 2,
        "previousRankSales": 4,
        "rankDelta": 2,
        "sales": 200.0,
        "previousSales": 100.0,
        "growthPct": 100.0,
        "sharePct": 20.0,
        "recoveredClients": 2,
        "droppedClients": 0,
        "clientBalance": 2,
        "mixCount": 3,
        "clients": 10,
        "avgTicket": 50.0,
        "quantity": 20.0,
        "top3ClientsSharePct": 40.0,
        "strongCategory": "Bebidas",
        "strongCategorySharePct": 35.0,
        "strongCategoryTeamSharePct": 28.0,
        "strongCategoryGapPct": 7.0,
        "weakCategory": "Limpieza",
        "weakCategoryGrowthPct": 5.0,
        "weakCategorySharePct": 4.0,
        "weakCategoryTeamSharePct": 12.0,
        "weakCategoryGapPct": -8.0,
        "objectiveFulfillmentPct": 105.0,
        "objectiveTarget": 190.0,
    }
    metrics.update(overrides)
    return metrics


def context():
    return {
        "teamGrowthPct": 10.0,
        "teamMixAverage": 2.0,
        "teamClientsAverage": 8.0,
        "teamTicketAverage": 60.0,
        "teamQuantityAverage": 15.0,
    }


def test_engine_limits_comments_and_includes_rule_version_and_evidence():
    result = CoachCommentEngine(DEFAULT_SELLER_RULES).evaluate(
        base_metrics(), context()
    )

    assert len(result["strengths"]) == 3
    assert len(result["opportunities"]) <= 3
    assert all(item["rule_id"] for item in result["strengths"])
    assert all(item["rule_version"] == 1 for item in result["strengths"])
    assert all(item["evidence"] for item in result["strengths"])
    assert all(item["evidence"] and item["action"] for item in result["opportunities"])


def test_kpi_change_replaces_growth_strength_with_drop_opportunity():
    engine = CoachCommentEngine(DEFAULT_SELLER_RULES)
    growing = engine.evaluate(base_metrics(growthPct=20), context())
    falling = engine.evaluate(base_metrics(growthPct=-20), context())

    assert any(
        item["rule_id"] == "SELLER_GROWTH_ABOVE_TEAM"
        for item in growing["strengths"]
    )
    assert not any(
        item["rule_id"] == "SELLER_GROWTH_ABOVE_TEAM"
        for item in falling["strengths"]
    )
    assert any(
        item["rule_id"] == "SELLER_SALES_DROP"
        for item in falling["opportunities"]
    )


def test_conflict_group_keeps_only_highest_priority_match():
    rules = [
        {
            "rule_id": "LOW",
            "version": 1,
            "priority": 10,
            "category": "strength",
            "condition": {"metric": "value", "operator": "gte", "value": 1},
            "message_template": "low {value}",
            "action_template": "low",
            "evidence_fields": ["value"],
            "conflict_group": "same",
        },
        {
            "rule_id": "HIGH",
            "version": 2,
            "priority": 90,
            "category": "opportunity",
            "condition": {"metric": "value", "operator": "gte", "value": 1},
            "message_template": "high {value}",
            "action_template": "high",
            "evidence_fields": ["value"],
            "conflict_group": "same",
        },
    ]

    result = CoachCommentEngine(rules).evaluate({"value": 5})

    assert not result["strengths"]
    assert [item["rule_id"] for item in result["opportunities"]] == ["HIGH"]


def test_rule_catalog_covers_required_commercial_topics():
    rule_ids = {rule["rule_id"] for rule in DEFAULT_SELLER_RULES}
    expected = {
        "SELLER_TOP_3_SALES",
        "SELLER_RANKING_IMPROVEMENT",
        "SELLER_GROWTH_ABOVE_TEAM",
        "SELLER_SALES_DROP",
        "SELLER_CLIENT_RECOVERY",
        "SELLER_HIGH_PARTICIPATION",
        "SELLER_MIX_ABOVE_TEAM",
        "SELLER_HIGH_CONCENTRATION",
        "SELLER_ACTIVE_CLIENTS_ABOVE_TEAM",
        "SELLER_NET_CLIENT_LOSS",
        "SELLER_TICKET_BELOW_TEAM",
        "SELLER_QUANTITY_ABOVE_TEAM",
        "SELLER_OBJECTIVE_MET",
        "SELLER_STRONG_CATEGORY",
        "SELLER_WEAK_CATEGORY",
        "SELLER_CROSS_SELL",
    }
    assert expected <= rule_ids


def test_repository_versions_configuration_and_audits_evaluation():
    db = mongomock.MongoClient().db
    repository = CoachRuleRepository(db)
    seeded = repository.seed_defaults()
    custom_rules = [dict(DEFAULT_SELLER_RULES[0], priority=99)]

    version = repository.create_version(
        "seller", custom_rules, "admin", "Umbral validado por negocio"
    )
    result = CoachCommentEngine(custom_rules).evaluate(base_metrics(), context())
    audit_id = repository.audit(
        user_id="director",
        entity_type="seller",
        entity_key="S1",
        date_range={"fechaDesde": "2026-07-01", "fechaHasta": "2026-07-31"},
        rule_set=version,
        result=result,
    )

    assert seeded["version"] == 1
    assert version["version"] == 2
    assert repository.active_rules("seller")["version"] == 2
    audits = repository.list_audits()
    assert audits[0]["audit_id"] == audit_id
    assert audits[0]["rule_set_version"] == 2


def test_missing_evidence_prevents_comment_generation():
    rule = {
        "rule_id": "MISSING_EVIDENCE",
        "version": 1,
        "priority": 1,
        "category": "strength",
        "condition": {"metric": "growthPct", "operator": "gte", "value": 1},
        "message_template": "Creció {growthPct}",
        "action_template": "Sostener",
        "evidence_fields": ["growthPct", "sourceValue"],
    }

    result = CoachCommentEngine([rule]).evaluate({"growthPct": 10})

    assert result["matchedRuleCount"] == 0
    assert result["strengths"] == []
