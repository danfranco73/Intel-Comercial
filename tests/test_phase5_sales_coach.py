from __future__ import annotations

from copy import deepcopy

import mongomock
import pytest

from security.models import AuthenticatedUser
from sales_coach.services.sales_coach_service import SalesCoachService


def make_user(role, seller_key=None):
    return AuthenticatedUser(
        id=role,
        email=f"{role}@example.test",
        name=role,
        role=role,
        seller_key=seller_key,
    )


def make_service(db, datasets):
    sales = deepcopy(datasets["sales"])
    return SalesCoachService(db, sales_loader=lambda *_args: (deepcopy(sales), "fixture"))


def test_seller_dashboard_has_separate_rankings_and_visible_formulas(datasets):
    db = mongomock.MongoClient().db
    service = make_service(db, datasets)

    result = service.sellers("2026-06-01", "2026-06-30", {})
    north = next(row for row in result["rows"] if row["sellerKey"] == "S1")

    assert north["rankSales"] == 1
    assert north["rankQuantity"] == 1
    assert north["rankActiveClients"] == 1
    assert "incrementalSales" in north
    assert "stabilityPct" in north
    assert "potentialEstimated" in north
    assert len(result["rankingDefinitions"]) == 4
    assert all(item["formula"] for item in result["rankingDefinitions"])


def test_seller_comments_include_numeric_evidence_and_actions(datasets):
    db = mongomock.MongoClient().db
    service = make_service(db, datasets)

    result = service.sellers("2026-06-01", "2026-06-30", {})
    north = next(row for row in result["rows"] if row["sellerKey"] == "S1")

    assert len(north["strengths"]) <= 3
    assert len(north["opportunities"]) <= 3
    assert all(item["evidence"] for item in north["strengths"])
    assert all(item["evidence"] and item["action"] for item in north["opportunities"])
    assert len(north["actionPlan"]) <= 3


def test_seller_cannot_open_another_seller_profile(datasets):
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_many(
        [
            {"seller_key": "S1", "seller_name": "Vendedora Norte"},
            {"seller_key": "S2", "seller_name": "Vendedor Sur"},
        ]
    )
    service = make_service(db, datasets)

    with pytest.raises(PermissionError, match="otro vendedor"):
        service.seller_detail(
            "S2",
            "2026-06-01",
            "2026-06-30",
            make_user("seller", seller_key="S1"),
            {"seller_name": ["Vendedora Norte"]},
        )


def test_seller_profile_contains_comparisons_evidence_and_clients(datasets):
    db = mongomock.MongoClient().db
    db.erp_sellers.insert_one(
        {
            "seller_key": "S1",
            "seller_name": "Vendedora Norte",
            "sales_force": "Minorista",
            "supervisor_key": "SUP1",
            "branch_key": "B1",
        }
    )
    service = make_service(db, datasets)

    result = service.seller_detail(
        "S1",
        "2026-06-01",
        "2026-06-30",
        make_user("seller", seller_key="S1"),
        {"seller_name": ["Vendedora Norte"]},
    )

    assert result["identification"]["sellerKey"] == "S1"
    assert result["comparisons"]["previousPeriodSales"] == 90.0
    assert result["kpis"]["sales"] == 180.0
    assert result["strengths"]
    assert result["actionPlan"]
    assert {item["clientKey"] for item in result["clients"]} == {"C1", "C3"}


def test_seller_cannot_open_client_from_another_portfolio(datasets):
    db = mongomock.MongoClient().db
    service = make_service(db, datasets)

    with pytest.raises(PermissionError, match="cartera"):
        service.client_detail(
            "C2",
            "2026-06-01",
            "2026-06-30",
            make_user("seller", seller_key="S1"),
            {"seller_name": ["Vendedora Norte"]},
        )


def test_client_profile_has_history_risk_mix_and_opportunities(datasets):
    db = mongomock.MongoClient().db
    service = make_service(db, datasets)

    result = service.client_detail(
        "C1",
        "2026-06-01",
        "2026-06-30",
        make_user("commercial_director"),
        {},
    )

    assert result["identification"]["clientKey"] == "C1"
    assert result["kpis"]["sales"] == 135.0
    assert result["kpis"]["previousSales"] == 90.0
    assert result["kpis"]["lastPurchase"] == "2026-06-10"
    assert len(result["history"]) == 2
    assert result["risk"]["evidence"]["currentSales"] == 135.0
    assert result["opportunities"][0]["evidence"]
