from bi.focus_dashboards import build_sellers_dashboard


def _record(seller, amount, quantity):
    return {
        "seller_name": seller,
        "seller_key": seller,
        "amount": amount,
        "quantity": quantity,
        "client_key": "C1",
        "invoice": f"{seller}-{amount}",
        "line": "Línea",
    }


def test_seller_comparison_exposes_money_and_volume_bases_separately():
    dashboard = build_sellers_dashboard(
        [_record("Ana", 200, 10)],
        [_record("Ana", 100, 20)],
        {"comparisonLabel": "Mes anterior"},
    )

    row = dashboard["rows"][0]
    assert row["sales"] == 200
    assert row["previousSales"] == 100
    assert row["growthPct"] == 100
    assert row["quantity"] == 10
    assert row["previousQuantity"] == 20
    assert row["quantityGrowthPct"] == -50
