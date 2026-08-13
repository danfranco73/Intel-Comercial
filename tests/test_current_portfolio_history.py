from datetime import datetime

from bi.facts import enrich_sales_records, sellers_with_active_scheme


def test_historical_sale_is_reassigned_to_current_client_portfolio():
    sales = [{
        "date": datetime(2026, 5, 10),
        "client_key": "C1",
        "seller_key": "OLD",
        "seller_name": "Gomez Marcelo",
        "product_key": "P1",
        "quantity": 10,
        "amount": 100,
    }]
    loaded = {
        "articles": {"records": []},
        "sellers": {"records": [
            {"seller_key": "OLD", "seller_name": "Gomez Marcelo"},
            {"seller_key": "NEW", "seller_name": "Vendedor Actual"},
        ]},
        "routes": {"records": [{
            "route_key": "R1",
            "seller_key": "NEW",
            "seller_name": "Vendedor Actual",
            "client_keys": ["C1"],
            "is_active": True,
            "valid_from": "2026-08-01",
            "valid_to": "2099-12-31",
        }]},
    }

    row = enrich_sales_records(sales, loaded)[0]

    assert row["seller_name"] == "Vendedor Actual"
    assert row["seller_key"] == "NEW"
    assert row["original_seller_name"] == "Gomez Marcelo"
    assert row["original_seller_key"] == "OLD"
    assert row["seller_assignment_source"] == "current_portfolio"


def test_original_seller_is_only_used_when_client_has_no_current_assignment():
    sales = [{
        "date": datetime(2026, 5, 10),
        "client_key": "C2",
        "seller_key": "OLD",
        "seller_name": "Gomez Marcelo",
        "product_key": "P1",
        "quantity": 10,
        "amount": 100,
    }]
    loaded = {
        "articles": {"records": []},
        "sellers": {"records": [{"seller_key": "OLD", "seller_name": "Gomez Marcelo"}]},
        "routes": {"records": []},
    }

    row = enrich_sales_records(sales, loaded)[0]

    assert row["seller_name"] == "Gomez Marcelo"
    assert row["seller_assignment_source"] == "sale_record_fallback"


def test_same_scheme_route_wins_over_newer_different_scheme_route():
    # Un cliente puede tener rutas activas simultáneas en dos esquemas
    # comerciales distintos (ej. minorista y mayorista). Que la ruta mayorista
    # se haya dado de alta más recientemente no debe pisar la venta minorista:
    # cada venta debe quedar con el vendedor vigente de SU PROPIO esquema.
    sales = [{
        "date": datetime(2026, 2, 10), "client_key": "C1", "seller_key": "OLD",
        "seller_name": "Gomez Marcelo", "sales_scheme_key": "F1",
        "product_key": "P1", "quantity": 10, "amount": 100,
    }]
    loaded = {
        "articles": {"records": []},
        "sellers": {"records": []},
        "routes": {"records": [
            {"seller_key": "OLD", "seller_name": "Gomez Marcelo", "sales_scheme_key": "F1", "client_keys": ["C1"], "is_active": True, "valid_from": "2023-10-01", "valid_to": "9999-12-31"},
            {"seller_key": "NEW", "seller_name": "Robles Richard Kaf", "sales_scheme_key": "F2", "client_keys": ["C1"], "is_active": True, "valid_from": "2026-03-03", "valid_to": "9999-12-31"},
        ]},
    }

    row = enrich_sales_records(sales, loaded)[0]

    assert row["seller_name"] == "Gomez Marcelo"
    assert row["seller_key"] == "OLD"


def test_cross_scheme_assignment_still_applies_when_client_left_its_own_scheme():
    # Si el cliente ya no tiene ninguna ruta activa en el esquema de la venta
    # (migró de esquema por completo), sí corresponde caer a la asignación
    # global más reciente entre esquemas.
    sales = [{
        "date": datetime(2026, 2, 10), "client_key": "C1", "seller_key": "OLD",
        "seller_name": "Gomez Marcelo", "sales_scheme_key": "F1",
        "product_key": "P1", "quantity": 10, "amount": 100,
    }]
    loaded = {
        "articles": {"records": []},
        "sellers": {"records": []},
        "routes": {"records": [
            {"seller_key": "OLD", "seller_name": "Gomez Marcelo", "sales_scheme_key": "F1", "client_keys": ["C1"], "is_active": False, "valid_from": "2023-10-01", "valid_to": "2026-01-31"},
            {"seller_key": "NEW", "seller_name": "Robles Richard Kaf", "sales_scheme_key": "F2", "client_keys": ["C1"], "is_active": True, "valid_from": "2026-03-03", "valid_to": "9999-12-31"},
        ]},
    }

    row = enrich_sales_records(sales, loaded)[0]

    assert row["seller_name"] == "Robles Richard Kaf"
    assert row["seller_key"] == "NEW"


def test_sellers_with_active_scheme_excludes_seller_without_matching_scheme():
    # Caso Gorrini: un vendedor cuya fuerza de venta vigente es FRESCOS no debe
    # quedar elegible cuando el informe se filtra por MERCADERIA, aunque herede
    # ventas históricas de ese esquema vía cartera actual (ver test de arriba).
    # Se usa el maestro de vendedores (sales_force/sales_scheme_name), no el
    # historial de rutas por cliente, que puede tener asociaciones superpuestas
    # o desactualizadas entre esquemas para un mismo cliente (caso real: un
    # vendedor con ruta histórica en un esquema que el ERP no actualizó tras
    # migrar de fuerza de venta).
    sellers = [
        {"seller_name": "Gorrini Jorge", "sales_scheme_name": "FRESCOS"},
        {"seller_name": "Giovanini Eduardo", "sales_force": "MERCADERIA"},
        {"seller_name": "Robles Richard Kaf", "sales_scheme_name": "MERCADERIA"},
    ]

    eligible = sellers_with_active_scheme(sellers, ["MERCADERIA"])

    assert eligible == {"giovanini eduardo", "robles richard kaf"}
    assert "gorrini jorge" not in eligible


def test_sellers_with_active_scheme_returns_none_when_no_scheme_filter():
    assert sellers_with_active_scheme([{"seller_name": "X", "is_active": True}], []) is None
    assert sellers_with_active_scheme([{"seller_name": "X", "is_active": True}], None) is None
