from copy import deepcopy
import pytest
from sales_coach.domain.deposit_attribution import DepositAttributionEngine
from sales_coach.repositories.attribution_rule_repository import AttributionRuleRepository


def rule(key="r100", **values):
    return {"rule_id": key, "version": 1, "priority": 300, "valid_from": None, "valid_to": None,
        "billing_company": ["1"], "sales_force": None, "route": ["100"], "channel": None,
        "product_scope": {}, "deposit_id": "4", "business_operation": "mayorista", "verified": True,
        "verified_by": "test", "notes": "synthetic", "simulation_enabled": True, **values}


def fact(**values):
    return {"date": "2026-09-28", "billing_company": "1", "route": "100", "sales_force": "2",
            "family": "ALMACEN", **values}


def test_specific_route_beats_general_product_scope_and_unknown_route_blocks_fallback():
    general = rule("warehouse", priority=100, route=None, product_scope={"family": ["ALMACEN"]},
                   deposit_id="1", business_operation="almacen")
    engine = DepositAttributionEngine([general, rule()])
    assert engine.evaluate(fact())["deposit_id"] == "4"
    assert engine.evaluate(fact(route="999"))["deposit_id"] == "1"
    result = engine.evaluate(fact(route=None))
    assert result["status"] == "UNATTRIBUTED" and result["reason"] == "UNRESOLVED_SPECIFIC_RULE"
    assert result["missing_fields"] == ["route"]


@pytest.mark.parametrize("route,deposit", [("200", "6"), ("201", "7"), ("202", "8"), ("203", "22"), ("204", "23"), ("205", "23")])
def test_confirmed_pymes_routes_require_company_and_preserve_separate_circuits(route, deposit):
    operation = "mayorista" if route == "204" else "minorista_drugstore" if route == "205" else "todo_pymes"
    engine = DepositAttributionEngine([rule("pymes-" + route, billing_company=["2"], route=[route],
        deposit_id=deposit, business_operation=operation)])
    result = engine.evaluate(fact(billing_company="2", route=route))
    assert (result["deposit_id"], result["business_operation"]) == (deposit, operation)
    assert engine.evaluate(fact(billing_company="1", route=route))["status"] == "UNATTRIBUTED"


def test_same_priority_disagreement_is_conflict_and_never_arbitrarily_resolved():
    engine = DepositAttributionEngine([rule(), rule("different", deposit_id="1")])
    assert engine.evaluate(fact())["status"] == "CONFLICT"
    assert engine.evaluate(fact())["deposit_id"] is None


def test_future_rule_does_not_leak_into_history_or_activate_without_a_date():
    future = rule("future", billing_company=["1"], route=None, sales_force=["3"], deposit_id="20",
                  simulation_enabled=False)
    assert DepositAttributionEngine([future]).evaluate(fact(sales_force="3"))["status"] == "UNATTRIBUTED"
    future.update(simulation_enabled=True, valid_from="2026-10-01", valid_to="2026-10-31")
    engine = DepositAttributionEngine([future])
    assert engine.evaluate(fact(sales_force="3"))["status"] == "UNATTRIBUTED"
    assert engine.evaluate(fact(sales_force="3", date="2026-10-01"))["deposit_id"] == "20"
    assert engine.evaluate(fact(sales_force="3", date="2026-11-01"))["status"] == "UNATTRIBUTED"


def test_pdev_frescos_requires_both_company_and_force():
    engine = DepositAttributionEngine([rule("pdev", billing_company=["3"], sales_force=["3"], route=None, deposit_id="20")])
    assert engine.evaluate(fact(billing_company="3", sales_force="3"))["deposit_id"] == "20"
    assert engine.evaluate(fact(billing_company="1", sales_force="3"))["status"] == "UNATTRIBUTED"
    assert engine.evaluate(fact(billing_company="3", sales_force=None))["status"] == "UNATTRIBUTED"


def test_rule_release_is_immutable_and_cannot_be_activated(tmp_path):
    repo = AttributionRuleRepository(tmp_path)
    release = {"version": 1, "mode": "simulation_only", "production_active": False, "rules": [rule()]}
    original = deepcopy(release)
    repo.save_release(release)
    repo.save_release(release)
    release["rules"][0]["deposit_id"] = "20"
    with pytest.raises(ValueError, match="Immutable"):
        repo.save_release(release)
    assert repo.load_release(1) == original
    release.update(version=2, production_active=True)
    with pytest.raises(ValueError, match="inactive"):
        repo.save_release(release)


def test_route_alone_and_duplicate_rule_versions_are_invalid():
    with pytest.raises(ValueError, match="alone"):
        DepositAttributionEngine([rule(billing_company=None)])
    with pytest.raises(ValueError, match="unique"):
        DepositAttributionEngine([rule(), rule()])


def test_pending_rule_requires_effective_date_and_version_increment(tmp_path):
    repo = AttributionRuleRepository(tmp_path)
    release = {"version": 1, "mode": "simulation_only", "production_active": False,
        "rules": [rule("future", simulation_enabled=False)]}
    repo.save_release(release)
    release["version"] = 2
    release["rules"][0].update(simulation_enabled=True, version=2)
    with pytest.raises(ValueError, match="effective date"):
        repo.save_release(release)
    release["rules"][0].update(valid_from="2026-10-01", version=1)
    with pytest.raises(ValueError, match="new rule version"):
        repo.save_release(release)
    release["rules"][0]["version"] = 2
    repo.save_release(release)
