from __future__ import annotations

from collections import defaultdict
from datetime import date

import pytest

from analyzer import analyze_datasets, apply_filters, dimension_value, metric_value
from bi.facts import enrich_sales_records
from bi.series_clickhouse import (
    SERIES_FILTER_FIELDS,
    SeriesUnavailable,
    article_dimension_rows,
    build_series_report,
    ensure_article_dimension,
)
import bi.series_clickhouse as series_module


class PythonSeriesQuery:
    """Las consultas de SeriesQuery resueltas sobre filas enriquecidas en Python.

    Implementa la semántica que el SQL debe tener (sumas y clientes distintos por
    mes y dimensión) para comparar el armado del informe contra el informe completo.
    """

    SUMS = ["quantity", "amount_net", "amount_final", "amount_net_internal"]

    def __init__(self, records):
        self.records = records

    def _value(self, item, field):
        if field in {"year", "month"}:
            return str(item[field])
        return dimension_value(item, field)

    def _rows(self, start, end, filters, exclude=None):
        active = {field: values for field, values in filters.items() if field != exclude}
        _, rows = apply_filters(self.records, active)
        return [item for item in rows if start <= item["date"] <= end]

    def _aggregate(self, rows):
        sums = [sum(metric_value(item, metric) for item in rows) for metric in self.SUMS]
        clients = {item["client_canonical"] for item in rows}
        orders = {item["invoice"] for item in rows if item.get("invoice")}
        return sums, clients, orders

    def totals_by_month(self, start, end, filters):
        grouped = defaultdict(list)
        for item in self._rows(start, end, filters):
            grouped[item["period"]].append(item)
        result = []
        for period, rows in sorted(grouped.items()):
            sums, clients, orders = self._aggregate(rows)
            result.append((period, *sums, len(clients), len(orders), len(rows)))
        return result

    def period_totals(self, start, end, filters):
        rows = self._rows(start, end, filters)
        sums, clients, orders = self._aggregate(rows)
        return (*sums, len(clients), len(orders), len(rows))

    def series_by_dimension(self, start, end, filters, with_period):
        grouped = defaultdict(list)
        for item in self._rows(start, end, filters):
            for field in series_module.SERIES_DIMENSIONS:
                key = (field, self._value(item, field), item["period"]) if with_period else (field, self._value(item, field))
                grouped[key].append(item)
        result = []
        for key, rows in grouped.items():
            sums, clients, _ = self._aggregate(rows)
            result.append((*key, *sums, len(clients)))
        return result

    def facet_counts(self, start, end, filters):
        counts = {}
        for field in SERIES_FILTER_FIELDS:
            for item in self._rows(start, end, filters, exclude=field):
                value = self._value(item, field)
                counts.setdefault(field, {})[value] = counts.get(field, {}).get(value, 0) + 1
        return counts


@pytest.fixture
def caliber_datasets(datasets):
    datasets["articles"]["records"] = [
        {**article, "caliber": "500ml" if article["product_key"] == "P1" else "1L"}
        for article in datasets["articles"]["records"]
    ]
    return datasets


def python_query(datasets):
    records = enrich_sales_records(list(datasets["sales"]["records"]), {"articles": datasets["articles"]})
    for record in records:
        # La venta de CH no trae product_name: sale del maestro de artículos.
        record["product_name"] = next(
            (a["product_name"] for a in datasets["articles"]["records"] if a["product_key"] == record["product_key"]),
            "Sin producto",
        )
    return PythonSeriesQuery(records)


JUNE = (date(2026, 6, 1), date(2026, 6, 30))


def test_series_report_matches_the_full_report(caliber_datasets):
    legacy = analyze_datasets(caliber_datasets)["interactiveReport"]
    report = build_series_report(python_query(caliber_datasets), *JUNE, {})
    series = report["interactiveReport"]

    assert series["labels"] == legacy["labels"]
    assert [m["key"] for m in series["metrics"]] == [m["key"] for m in legacy["metrics"]]
    legacy_totals = {item["metric"]: item for item in legacy["totals"]}
    for item in series["totals"]:
        assert item["series"] == legacy_totals[item["metric"]]["series"]
        assert item["total"] == legacy_totals[item["metric"]]["total"]
    for field in ["family", "line", "product_name", "brand", "caliber", "supplier", "channel"]:
        for metric in series["byDimension"][field]:
            ours = sorted((row["name"], row["total"], row["data"]) for row in series["byDimension"][field][metric])
            theirs = sorted((row["name"], row["total"], row["data"]) for row in legacy["byDimension"][field][metric])
            assert ours == theirs, (field, metric)


def test_series_summary_and_filters(caliber_datasets):
    report = build_series_report(python_query(caliber_datasets), *JUNE, {"caliber": ["1L"]})
    # Junio, calibre 1L (Producto Beta): C3 45 + C2 90 de neto, dos clientes.
    assert report["summary"]["salesCurrent"] == 135
    assert report["summary"]["activeClients"] == 2
    assert report["summary"]["ordersCurrent"] == 2
    assert report["appliedFilters"] == {"caliber": ["1L"]}
    caliber_options = [option["value"] for option in report["availableFilters"]["caliber"]["options"]]
    assert caliber_options == ["1L", "500ml"]
    family_options = [option["value"] for option in report["availableFilters"]["family"]["options"]]
    assert family_options == ["Familia B"]
    assert [option["value"] for option in report["availableFilters"]["month"]["options"]] == [6]


def test_series_comparison_is_only_computed_on_request(caliber_datasets):
    query = python_query(caliber_datasets)
    plain = build_series_report(query, *JUNE, {})
    assert "salesPrevious" not in plain["summary"]
    compared = build_series_report(query, *JUNE, {}, compare=True)
    assert compared["meta"]["comparison"]["comparisonStart"] == "2025-06-01"
    assert compared["summary"]["salesPrevious"] == 0
    assert compared["summary"]["salesGrowthPct"] is None


def test_seller_filters_are_left_to_the_full_report(caliber_datasets):
    with pytest.raises(SeriesUnavailable):
        build_series_report(python_query(caliber_datasets), *JUNE, {"seller_name": ["Vendedora Norte"]})


def test_article_dimension_rows_apply_master_defaults():
    rows = article_dimension_rows(
        [{"product_key": "P1", "product_name": "", "family": "Familia A"}, {"product_key": ""}],
        "v1",
    )
    assert rows == [["P1", "P1", "Familia A", "Sin línea", "Sin marca", "Sin calibre", "Sin unidad de negocio", "Sin proveedor", "v1"]]


class FakeClient:
    def __init__(self, stored=("", 0)):
        self.stored = stored
        self.commands = []
        self.inserts = []

    def command(self, sql):
        self.commands.append(sql)

    def query(self, sql, parameters=None):
        return type("Result", (), {"result_rows": [self.stored]})()

    def insert(self, table, rows, column_names):
        self.inserts.append((table, rows))


def test_article_dimension_reloads_only_when_master_version_changes(monkeypatch):
    monkeypatch.setattr(series_module, "_dim_loaded_version", None)
    loads = []
    articles = lambda: loads.append(1) or [{"product_key": "P1", "family": "F"}]

    fresh = FakeClient(stored=("v1", 10))
    ensure_article_dimension(fresh, "db", "v1", articles)
    assert loads == [] and fresh.inserts == []

    stale = FakeClient(stored=("v1", 10))
    ensure_article_dimension(stale, "db", "v2", articles)
    assert loads == [1]
    assert stale.inserts[0][0] == "db.dim_articles_staging"
    assert any(command.startswith("EXCHANGE TABLES db.dim_articles AND db.dim_articles_staging") for command in stale.commands)

    ensure_article_dimension(stale, "db", "v2", articles)
    assert loads == [1]


def _series_handler(payload, scope=None):
    import gzip
    import json
    from types import SimpleNamespace

    from sales_coach.server import AppHandler

    handler = AppHandler.__new__(AppHandler)
    handler.data_scope = scope
    handler.auth_context = SimpleNamespace(user=SimpleNamespace(id="u1"))
    handler._read_json_body = lambda: payload
    handler.log_application_error = lambda *args, **kwargs: None
    handler.sent = []
    handler.send_json = lambda body, status=200, headers=None: handler.sent.append((status, body))
    handler.send_precompressed_json = lambda body, status=200, headers=None: handler.sent.append(
        (status, json.loads(gzip.decompress(body)))
    )
    return handler


SERIES_PAYLOAD = {"fechaDesde": "2026-06-01", "fechaHasta": "2026-06-30", "filters": {}}


def test_series_endpoint_falls_back_for_scoped_users_and_without_clickhouse(monkeypatch):
    import sales_coach.routes.analysis_routes as routes

    monkeypatch.setattr(routes, "clickhouse_configured", lambda: True)
    scoped = _series_handler(SERIES_PAYLOAD, scope={"seller_name": ["Vendedora Norte"]})
    scoped.handle_bi_series()
    assert scoped.sent[0][0] == 409 and scoped.sent[0][1]["fallback"] == "legacy"

    monkeypatch.setattr(routes, "clickhouse_configured", lambda: False)
    no_clickhouse = _series_handler(SERIES_PAYLOAD)
    no_clickhouse.handle_bi_series()
    assert no_clickhouse.sent[0][0] == 409


def test_series_endpoint_serves_and_caches_the_report(monkeypatch, caliber_datasets):
    import sales_coach.routes.analysis_routes as routes

    query = python_query(caliber_datasets)
    calls = []

    def fake_report(fecha_desde, fecha_hasta, filters, **kwargs):
        calls.append(filters)
        return build_series_report(query, date.fromisoformat(fecha_desde), date.fromisoformat(fecha_hasta), filters)

    monkeypatch.setattr(routes, "clickhouse_configured", lambda: True)
    monkeypatch.setattr(routes, "build_clickhouse_series_report", fake_report)
    monkeypatch.setattr(routes, "get_erp_storage_status", lambda: {"articlesLastSyncAt": "v1"})
    monkeypatch.setattr(routes, "get_clickhouse_storage_status", lambda: {"lastSyncAt": "s1"})

    first = _series_handler({**SERIES_PAYLOAD, "supplierFocus": "Proveedor B"})
    first.handle_bi_series()
    second = _series_handler({**SERIES_PAYLOAD, "supplierFocus": "Proveedor B"})
    second.handle_bi_series()

    assert first.sent[0][0] == 200
    assert first.sent[0][1]["summary"]["salesCurrent"] == 135
    assert calls == [{"supplier": ["Proveedor B"]}]
    assert second.sent[0][1] == first.sent[0][1]
