from __future__ import annotations

from openpyxl import Workbook

from analyzer import load_dataset_source


def test_excel_sales_loading(tmp_path):
    path = tmp_path / "ventas_anonimas.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ventas"
    sheet.append(["Fecha", "Cliente", "Importe", "Vendedor"])
    sheet.append(["2026-06-10", "C1", 150, "Vendedora Norte"])
    workbook.save(path)

    loaded = load_dataset_source(
        "sales",
        {"path": path, "file": "ventas_anonimas.xlsx", "sheet": "Ventas", "headerRow": 0},
        {"date": 0, "client_key": 1, "amount": 2, "seller_name": 3},
    )
    assert loaded["rowsValid"] == 1
    assert loaded["records"][0]["amount"] == 150
