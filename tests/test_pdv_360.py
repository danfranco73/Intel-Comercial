from __future__ import annotations

from datetime import date

import mongomock
import pytest

from sales_coach.services.pdv_360_service import (
    Pdv360Service,
    build_pdv_360,
    build_portfolio,
    build_windows,
    classify_recency,
)

END = date(2026, 9, 28)
WINDOWS = build_windows(END, 90)

ARTICLES = {
    "B1": {"product_key": "B1", "product_name": "Gaseosa 1", "business_unit": "BEBIDAS"},
    "B2": {"product_key": "B2", "product_name": "Gaseosa 2", "business_unit": "BEBIDAS"},
    "B3": {"product_key": "B3", "product_name": "Gaseosa 3", "business_unit": "BEBIDAS"},
    "B4": {"product_key": "B4", "product_name": "Gaseosa 4", "business_unit": "BEBIDAS"},
    "C1": {"product_key": "C1", "product_name": "Queso", "business_unit": "CAMARA"},
    "M1": {"product_key": "M1", "product_name": "Fideos", "business_unit": "MOLINOS"},
}


def line(day, product, amount, qty=1, company="CODENOA S.R.L.", force="MERCADERIA", seller="VEND M", invoice=None):
    return {
        "date": day,
        "product_key": product,
        "invoice": invoice or f"F-{day.isoformat()}-{product}",
        "company_name": company,
        "sales_force": force,
        "seller_name": seller,
        "business_type": "ALM",
        "channel": "ALM",
        "client_name": "Almacén Demo",
        "amount_net": amount,
        "quantity": qty,
    }


def peer(client, product, amount, company, force, qty=1):
    return {
        "client_key": client,
        "product_key": product,
        "company_name": company,
        "sales_force": force,
        "amount_net": amount,
        "quantity": qty,
    }


def _peers():
    rows = []
    # 4 pares: todos compran bebidas (3 SKU vía BEBIDAS) y 3 compran cámara vía FRESCOS/PDEV.
    for client in ("P1", "P2", "P3", "P4"):
        for product in ("B1", "B2", "B3"):
            rows.append(peer(client, product, 100, "CODENOA S.R.L.", "BEBIDAS"))
        rows.append(peer(client, "M1", 50, "CODENOA S.R.L.", "MERCADERIA"))
    for client in ("P1", "P2", "P3"):
        rows.append(peer(client, "C1", 200, "PDEV S.A.S.", "FRESCOS"))
    return rows


ROUTES = [
    {"is_active": True, "sales_force": "MERCADERIA", "route_description": "R-M", "seller_name": "VEND M", "visit_days": "2", "valid_from": "2026-01-01"},
    {"is_active": True, "sales_force": "FRESCOS", "route_description": "R-F", "seller_name": "VEND F", "visit_days": "3,6", "valid_from": "2026-01-01"},
    {"is_active": False, "sales_force": "BEBIDAS", "route_description": "R-B-VIEJA", "seller_name": "VEND B", "visit_days": "4", "valid_from": "2024-01-01"},
]


def _build(client_lines, routes=ROUTES, peers=None):
    return build_pdv_360(
        client_key="C100",
        client_lines=client_lines,
        peer_lines=_peers() if peers is None else peers,
        routes=routes,
        articles=ARTICLES,
        windows=WINDOWS,
        last_purchase=max((item["date"] for item in client_lines), default=None),
        first_purchase=min((item["date"] for item in client_lines), default=None),
        company_universe=["CODENOA S.R.L.", "ERDASER S.R.L", "PDEV S.A.S.", "TODO PYMES SRL"],
    )


def test_portfolio_by_company_and_unit_compares_against_peers():
    result = _build([
        line(date(2026, 9, 1), "B1", 300, force="MERCADERIA"),
        line(date(2026, 9, 2), "M1", 100),
    ])
    companies = {item["company"]: item for item in result["companies"]}
    assert companies["CODENOA S.R.L."]["skus"] == 2
    assert companies["PDEV S.A.S."]["status"] == "Nunca compró"
    assert companies["PDEV S.A.S."]["peerPenetrationPct"] == 75.0
    assert companies["ERDASER S.R.L"]["skus"] == 0  # siempre se listan las 4 empresas

    units = {item["unit"]: item for item in result["businessUnits"]}
    assert units["BEBIDAS"]["skus"] == 1
    assert units["BEBIDAS"]["peerAvgSkus"] == 3.0
    assert units["BEBIDAS"]["peerVia"] == ["BEBIDAS"]
    assert units["CAMARA"]["skus"] == 0
    assert result["kpis"]["companiesBought"] == 1
    assert result["meta"]["peerClients"] == 4


def test_opportunities_explain_how_to_reach_the_pdv():
    result = _build([
        line(date(2026, 9, 1), "B1", 300, force="MERCADERIA"),
        line(date(2026, 9, 2), "M1", 100),
    ])
    by_title = {item["title"]: item for item in result["opportunities"]}
    camara = by_title["No compra CAMARA"]
    assert "75.0%" in camara["detail"]
    # Está en ruta de FRESCOS, que es por donde sus pares compran cámara.
    assert camara["how"].startswith("FRESCOS: VEND F · ruta R-F")

    bebidas = by_title["Amplitud baja en BEBIDAS"]
    # Sus pares compran bebidas por BEBIDAS y la ruta de BEBIDAS está inactiva.
    assert "no está en ninguna ruta" in bebidas["how"]
    assert "Hoy lo atiende MERCADERIA" in bebidas["how"]
    # El hueco de PDEV ya está explicado por CAMARA: no se duplica.
    assert "No compra nada de PDEV S.A.S." not in by_title
    assert [item["priority"] for item in result["opportunities"]] == list(range(1, len(result["opportunities"]) + 1))


def test_coverage_flags_route_without_sales():
    result = _build([line(date(2026, 9, 2), "M1", 100)])
    coverage = {item["salesForce"]: item for item in result["coverage"]}
    assert coverage["MERCADERIA"]["diagnosis"].startswith("Atendido")
    assert coverage["FRESCOS"]["onRoute"] is True
    assert coverage["FRESCOS"]["diagnosis"] == "En ruta pero sin compras en la ventana"
    assert coverage["BEBIDAS"]["onRoute"] is False  # su única ruta está inactiva
    # La visita sin venta de FRESCOS se suma a la brecha de CAMARA (que se
    # compra por FRESCOS) en vez de duplicarse.
    camara = next(item for item in result["opportunities"] if item["title"] == "No compra CAMARA")
    assert "Está en la ruta R-F de FRESCOS y no compró en 90 días." in camara["detail"]
    assert not any(item["type"] == "visited_no_sale" for item in result["opportunities"])


def test_routes_without_seller_or_baja_do_not_count_as_coverage():
    routes = [
        {"is_active": True, "sales_force": "MERCADERIA", "route_description": "R-M", "seller_name": "VEND M"},
        {"is_active": True, "sales_force": "BEBIDAS", "route_description": "ZONA DE BAJA", "seller_name": "X"},
        {"is_active": True, "sales_force": "FRESCOS", "route_description": "10", "seller_name": ""},
    ]
    result = _build([line(date(2026, 9, 2), "M1", 100)], routes=routes)
    coverage = {item["salesForce"]: item for item in result["coverage"]}
    assert coverage["MERCADERIA"]["onRoute"] is True
    assert coverage["BEBIDAS"]["onRoute"] is False
    assert coverage["FRESCOS"]["onRoute"] is False


def test_returns_net_of_devolutions_and_lost_habitual_skus():
    result = _build([
        # B2 habitual en la ventana anterior (2 meses), no repite: perdido.
        line(date(2026, 5, 10), "B2", 100),
        line(date(2026, 6, 10), "B2", 100),
        # B3 suelto en la ventana anterior: rota, no es pérdida.
        line(date(2026, 6, 12), "B3", 100),
        # B1 comprado y devuelto en la ventana: no cuenta como SKU.
        line(date(2026, 9, 1), "B1", 100, qty=2),
        line(date(2026, 9, 3), "B1", -100, qty=-2),
        line(date(2026, 9, 2), "M1", 100),
    ])
    assert result["kpis"]["skus"] == 1
    assert result["kpis"]["sales"] == 100
    assert [item["productKey"] for item in result["skus"]["lost"]] == ["B2"]
    assert result["kpis"]["previousSkus"] == 2


def test_history_covers_24_months_split_by_company():
    result = _build([
        line(date(2026, 9, 2), "M1", 100),
        line(date(2026, 8, 2), "C1", 50, company="PDEV S.A.S.", force="FRESCOS"),
    ])
    assert len(result["history"]) == 24
    assert result["history"][-1] == {"period": "2026-09", "total": 100, "byCompany": {"CODENOA S.R.L.": 100}}
    assert result["history"][-2]["byCompany"] == {"PDEV S.A.S.": 50}


def test_windows_and_status():
    assert WINDOWS["current_start"] == date(2026, 7, 1)
    assert WINDOWS["previous_end"] == date(2026, 6, 30)
    assert WINDOWS["yoy_start"] == date(2025, 7, 1)
    assert WINDOWS["history_start"] == date(2024, 10, 1)
    assert classify_recency(10) == "Activo"
    assert classify_recency(45) == "Dormido"
    assert classify_recency(200) == "Perdido"
    assert classify_recency(None) == "Sin compras"


class _User:
    role = "seller"
    seller_key = "7"


def _service():
    db = mongomock.MongoClient().db
    db["erp_sellers"].insert_many([
        {"seller_key": "7", "seller_name": "VEND M"},
        {"seller_key": "9", "seller_name": "OTRO"},
    ])
    db["erp_articles"].insert_many([
        {"product_key": "B1", "business_unit": "BEBIDAS"},
        {"product_key": "C1", "business_unit": "CAMARA"},
    ])
    return Pdv360Service(db, clickhouse_factory=lambda: None, profile_loader=lambda _key: None)


def test_scope_conditions_filter_force_and_articles_but_not_sellers_for_the_ficha():
    service = _service()
    scope = {"seller_name": ["VEND M"], "sales_force": ["BEBIDAS"], "business_unit": ["BEBIDAS"]}
    conditions, params = service._scope_conditions(_User(), scope, restrict_sellers=False)
    assert params["scope_forces"] == ["BEBIDAS"]
    assert params["scope_products"] == ["B1"]
    assert not any("seller_key" in condition for condition in conditions)

    conditions, params = service._scope_conditions(_User(), scope)
    assert params["scope_sellers"] == ["7"]


def test_authorization_requires_sale_or_active_route_from_allowed_seller():
    service = _service()
    scope = {"seller_name": ["VEND M"]}
    service._authorize("C100", [line(END, "M1", 10)], [], _User(), scope)
    service._authorize("C100", [], [{"is_active": True, "seller_name": "VEND M"}], _User(), scope)
    with pytest.raises(PermissionError):
        service._authorize("C100", [line(END, "M1", 10, seller="OTRO")], [{"is_active": False, "seller_name": "VEND M"}], _User(), scope)
    # Admin (scope vacío) no se restringe.
    service._authorize("C100", [], [], _User(), {})


def _portfolio(selection, client_meta=None):
    lines = [
        # C1 (ALM, en ruta de MERCADERIA): compra fideos y 1 bebida.
        {**peer("C1", "M1", 100, "CODENOA S.R.L.", "MERCADERIA"), "seller_name": "VEND M"},
        {**peer("C1", "B1", 50, "CODENOA S.R.L.", "MERCADERIA"), "seller_name": "VEND M"},
        *[{**row, "seller_name": "VEND B"} for row in _peers()],
    ]
    meta = {key: {"name": key, "business_type": "ALM", "last_purchase": date(2026, 9, 20), "monthly_avg_12m": 0.0}
            for key in ("C1", "P1", "P2", "P3", "P4")}
    meta["C2"] = {"name": "Dormido", "business_type": "ALM", "last_purchase": date(2026, 3, 1), "monthly_avg_12m": 300.0}
    meta.update(client_meta or {})
    routes = {
        "C1": [ROUTES[0]],
        "C2": [ROUTES[0]],
    }
    return build_portfolio(
        lines=lines,
        client_meta=meta,
        routes_by_client=routes,
        articles=ARTICLES,
        selection=set(selection),
        window_end=END,
        window_days=90,
        company_universe=["CODENOA S.R.L.", "PDEV S.A.S."],
    )


def test_portfolio_worklist_and_matrix_across_clients():
    result = _portfolio({"C1", "C2"})
    assert result["summary"]["clients"] == 2
    assert result["summary"]["activeClients"] == 1

    rows = {row["clientKey"]: row for row in result["matrix"]}
    assert rows["C1"]["units"]["CAMARA"]["flag"] == "gap"
    assert rows["C1"]["units"]["BEBIDAS"]["flag"] == "low"
    assert rows["C1"]["units"]["MOLINOS"]["flag"] == "ok"
    assert rows["C1"]["companies"]["PDEV S.A.S."]["flag"] == "gap"
    assert rows["C1"]["routes"] == [{"salesForce": "MERCADERIA", "route": "R-M", "seller": "VEND M"}]

    # C2 no compró en la ventana: una sola oportunidad, con su propio promedio.
    c2 = [item for item in result["worklist"] if item["clientKey"] == "C2"]
    assert [item["type"] for item in c2] == ["inactive"]
    assert c2[0]["potential"] == 900.0  # 300/mes x 90 días
    assert c2[0]["channel"]["seller"] == "VEND M"

    c1_titles = {item["title"] for item in result["worklist"] if item["clientKey"] == "C1"}
    assert {"No compra CAMARA", "Amplitud baja en BEBIDAS"} <= c1_titles
    potentials = [item["potential"] for item in result["worklist"]]
    assert potentials == sorted(potentials, reverse=True)


def test_portfolio_penetration_compares_selection_against_expected():
    result = _portfolio({"C1"})
    units = {item["unit"]: item for item in result["penetration"]["units"]}
    # Los pares ALM (C1 + 4 pares) compran cámara en 3 de 5.
    assert units["CAMARA"]["penetrationPct"] == 0.0
    assert units["CAMARA"]["expectedPct"] == 60.0
    assert units["BEBIDAS"]["penetrationPct"] == 100.0
