from datetime import datetime

from bi.structural_dashboards import build_history_dashboard


def _record(period, line, seller, quantity, amount):
    return {
        "period": period,
        "date": datetime.strptime(f"{period}-15", "%Y-%m-%d"),
        "line": line,
        "family": line,
        "seller_name": seller,
        "quantity": quantity,
        "amount": amount,
        "client": seller,
        "client_canonical": seller.lower(),
        "invoice": f"{period}-{line}-{seller}",
    }


def test_next_month_budget_uses_three_month_units_and_latest_unit_value():
    records = [
        _record("2026-05", "Lácteos", "Ana", 80, 800),
        _record("2026-06", "Lácteos", "Ana", 100, 1200),
        _record("2026-07", "Lácteos", "Ana", 120, 1800),
        _record("2026-07", "Lácteos", "Beto", 0, 0),
    ]
    planning = {"budget": {"volumeGrowthPct": 10, "dimensionShares": {"seller": {"Ana": 60, "Beto": 40}}}}
    context = {"selectedEnd": datetime(2026, 7, 31)}

    forecast = build_history_dashboard(records, records, context, planning)["budget"]["forecast"]

    assert forecast["period"] == "2026-08"
    assert forecast["totalQuantity"] == 110
    assert forecast["totalSales"] == 1650
    assert forecast["lines"][0]["lastUnitValue"] == 15
    assert [row["targetSales"] for row in forecast["sellers"]] == [990, 660]
    assert forecast["sellerShareTotalPct"] == 100


def test_budget_defaults_seller_percentages_to_latest_month_sales_mix():
    records = [
        _record("2026-06", "Línea", "Ana", 10, 100),
        _record("2026-07", "Línea", "Ana", 10, 150),
        _record("2026-07", "Línea", "Beto", 10, 50),
    ]
    context = {"selectedEnd": datetime(2026, 7, 31)}

    forecast = build_history_dashboard(records, records, context, {})["budget"]["forecast"]

    shares = {row["label"]: row["sharePct"] for row in forecast["sellers"]}
    assert shares == {"Ana": 75.0, "Beto": 25.0}
    assert sum(row["targetSales"] for row in forecast["sellers"]) == forecast["totalSales"]


def test_stale_seller_percentages_are_replaced_after_portfolio_change():
    records = [
        _record("2026-06", "Línea", "Vendedor Actual", 10, 100),
        _record("2026-07", "Línea", "Vendedor Actual", 10, 150),
    ]
    planning = {"budget": {"dimensionShares": {"seller": {"Gomez Marcelo": 50, "Vendedor Actual": 50}}}}
    context = {"selectedEnd": datetime(2026, 7, 31)}

    forecast = build_history_dashboard(records, records, context, planning)["budget"]["forecast"]

    assert [row["label"] for row in forecast["sellers"]] == ["Vendedor Actual"]
    assert forecast["sellers"][0]["sharePct"] == 100
