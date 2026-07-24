from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def sales_records():
    records = load_fixture("sales.json")
    for record in records:
        record["date"] = date.fromisoformat(record["date"])
    return records


@pytest.fixture
def datasets(sales_records):
    return {
        "sales": {
            "datasetType": "sales",
            "sourceKind": "fixture",
            "file": "ventas_anonimas.json",
            "sheet": "Ventas",
            "headerRow": 0,
            "rowsRead": len(sales_records),
            "rowsValid": len(sales_records),
            "records": sales_records,
            "analysisRange": {"fechaDesde": "2026-06-01", "fechaHasta": "2026-06-30"},
            "comparisonRange": {"fechaDesde": "2026-05-01", "fechaHasta": "2026-05-31"},
        },
        "articles": {
            "datasetType": "articles",
            "file": "articulos_anonimos.json",
            "sheet": "Artículos",
            "records": load_fixture("articles.json"),
        },
        "sellers": {
            "datasetType": "sellers",
            "file": "vendedores_anonimos.json",
            "sheet": "Vendedores",
            "records": load_fixture("sellers.json"),
        },
        "routes": {
            "datasetType": "routes",
            "file": "rutas_anonimas.json",
            "sheet": "Rutas",
            "records": load_fixture("routes.json"),
        },
    }
