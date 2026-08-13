from datetime import datetime

from bi.facts import enrich_sales_records


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
