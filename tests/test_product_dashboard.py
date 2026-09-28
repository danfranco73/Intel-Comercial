from bi.focus_dashboards import build_product_dashboard


def _record(family, line, brand, seller, client, amount, quantity):
    return {
        "family": family,
        "line": line,
        "brand": brand,
        "seller_name": seller,
        "client_key": client,
        "client": f"Cliente {client}",
        "amount": amount,
        "quantity": quantity,
        "invoice": f"{client}-{amount}",
    }


def test_family_dashboard_opens_lines_clients_and_sellers():
    current = [
        _record("Bebidas", "Gaseosas", "Marca A", "Ana", "C1", 300, 10),
        _record("Bebidas", "Aguas", "Marca B", "Beto", "C2", 100, 5),
        _record("Almacén", "Harinas", "Marca C", "Ana", "C1", 100, 2),
    ]
    previous = [
        _record("Bebidas", "Gaseosas", "Marca A", "Ana", "C1", 200, 8),
        _record("Bebidas", "Gaseosas", "Marca A", "Ana", "C3", 50, 1),
    ]

    dashboard = build_product_dashboard(current, previous, "family", "line")

    assert dashboard["summary"]["count"] == 2
    bebidas = dashboard["rows"][0]
    assert bebidas["label"] == "Bebidas"
    assert bebidas["sales"] == 400
    assert bebidas["previousSales"] == 250
    assert bebidas["growthPct"] == 60
    assert bebidas["sharePct"] == 80
    assert bebidas["newClients"] == 1
    assert bebidas["clients"] == 2
    assert bebidas["previousClients"] == 2
    assert bebidas["clientsGrowthPct"] == 0
    gaseosas = bebidas["children"][0]
    assert gaseosas["clients"] == 1
    assert gaseosas["previousClients"] == 2
    assert gaseosas["clientsGrowthPct"] == -50
    assert dashboard["summary"]["previousClients"] == 2
    assert bebidas["clientsWithoutPurchase"] == 1
    assert [child["label"] for child in bebidas["children"]] == ["Gaseosas", "Aguas"]
    assert bebidas["children"][0]["mixPct"] == 75
    assert bebidas["children"][0]["growthPct"] == 20
    assert bebidas["topClients"][0]["label"] == "Cliente C1"
    assert all(client["sales"] > 0 for client in bebidas["topClients"])
    assert [seller["label"] for seller in bebidas["topSellers"]] == ["Ana", "Beto"]


def test_line_dashboard_opens_brands():
    current = [
        _record("Bebidas", "Gaseosas", "Marca A", "Ana", "C1", 300, 10),
        _record("Bebidas", "Gaseosas", None, "Ana", "C2", 100, 10),
    ]

    dashboard = build_product_dashboard(current, [], "line", "brand")

    row = dashboard["rows"][0]
    assert row["label"] == "Gaseosas"
    assert {child["label"] for child in row["children"]} == {"Marca A", "Sin marca"}
