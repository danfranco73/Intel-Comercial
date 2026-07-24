from __future__ import annotations

from datetime import date

from analyzer import analyze_datasets
from clickhouse_client import _compact_records
from erp_client import normalize_erp_sale_row
from mongo_client import _compact_sale_record


def test_normalize_chess_sale_preserves_commercial_identity():
    record = normalize_erp_sale_row(
        {
            "fechaComprobate": "2026-06-10",
            "idCliente": 101,
            "nombreCliente": "Cliente Anónimo",
            "idVendedor": 7,
            "dsVendedor": "Vendedora Anónima",
            "idArticulo": 55,
            "desArticulo": "Producto Anónimo",
            "idDocumento": 8,
            "letra": "A",
            "serie": "1",
            "nrodoc": "99",
            "subtotalFinal": "1.234,50",
            "subtotalNeto": "1.100,00",
            "cantidadesTotal": "12",
            "desRuta": "Ruta Demo",
            "idSucursal": 3,
            "dsSucursal": "Sucursal Demo",
            "idDeposito": 8,
            "dsDeposito": "Depósito Demo",
        }
    )
    assert record["date"] == date(2026, 6, 10)
    assert record["client_key"] == "101"
    assert record["seller_key"] == "7"
    assert record["amount"] == 1234.5
    assert record["invoice"] == "8-A-1-99"
    assert record["branch_key"] == "3"
    assert record["branch_name"] == "Sucursal Demo"
    assert record["deposit_key"] == "8"
    assert record["deposit_name"] == "Depósito Demo"


def test_compaction_groups_equivalent_sales(sales_records):
    duplicate = dict(sales_records[2])
    duplicate["amount"] = 25
    compacted = _compact_records([sales_records[2], duplicate])
    assert len(compacted) == 1
    assert compacted[0]["amount"] == 175

    mongo_compact = _compact_sale_record(sales_records[2])
    assert mongo_compact["client_key"] == "C1"
    assert "line_key" not in mongo_compact


def test_existing_kpis_rankings_mix_opportunities_and_monthly_evolution(datasets):
    result = analyze_datasets(datasets)
    assert result["summary"]["salesCurrent"] == 300
    assert result["meta"]["rowsAnalyzed"] == 3
    sellers = result["dashboards"]["sellers"]
    assert sellers["rows"][0]["seller"] == "Vendedora Norte"
    assert sellers["summary"]["sales"] == sum(row["sales"] for row in sellers["rows"])
    assert sellers["summary"]["valuePerQuantity"] > 0
    assert sellers["lineMix"]
    assert sellers["rows"][0]["lineMix"][0]["rank"] >= 1
    assert sellers["rows"][0]["clients"] >= 1
    assert sellers["rows"][0]["newClients"] >= 0
    assert sellers["rows"][0]["clientsWithoutPurchase"] >= 0
    assert 1 <= len(sellers["rows"][0]["topClients"]) <= 10
    assert sellers["rows"][0]["topClients"][0]["sales"] > 0
    assert result["charts"]["brandMoney"][0]["label"] in {"Marca A", "Marca B"}
    assert result["dashboards"]["history"]["rows"][-1]["label"] == "2026-06"
    assert "opportunities" in result


def test_seller_scope_is_applied_before_facets(datasets):
    result = analyze_datasets(datasets, scope_filters={"seller_name": ["Vendedora Norte"]})
    assert result["summary"]["salesCurrent"] == 200
    assert result["meta"]["rowsUniverse"] == 2
    seller_options = result["availableFilters"]["seller_name"]["options"]
    assert [option["value"] for option in seller_options] == ["Vendedora Norte"]
