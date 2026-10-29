"""Series de Comercial calculadas en ClickHouse.

El informe completo (analyzer.analyze_datasets) trae cada fila de venta a Python
y la cruza con los maestros: ~1 GB por mes analizado. Las vistas de Comercial
(series de producto, proveedores y canales, clientes compradores) solo necesitan
sumas y conteos de clientes distintos por mes y por dimensión, así que se piden
ya totalizadas a ClickHouse y el resultado ocupa unos pocos miles de números.

Las reglas replican bi/facts.enrich_sales_records y
analyzer.build_interactive_filter_report:
- la venta de referencia es el neto sin IVA (amount_net);
- el cliente se identifica por el nombre normalizado (client_canonical);
- los atributos de producto salen del maestro de artículos (copiado a
  dim_articles) con los mismos textos por defecto ("Sin familia", ...);
- el canal sale de la venta ("Sin canal" si viene vacío).
"""
from __future__ import annotations

from datetime import date
from threading import Lock

from analyzer import (
    FILTER_DEFINITIONS,
    INTERACTIVE_REPORT_BUYERS_METRIC,
    INTERACTIVE_REPORT_METRICS,
    filter_label,
    month_labels_between,
    normalize_filter_values,
    period_filter_values,
    sort_filter_values,
    summarize_filters,
)


SERIES_DIMENSIONS = ["family", "line", "product_name", "brand", "caliber", "supplier", "channel"]
SERIES_FILTER_FIELDS = ["year", "month", "family", "line", "product_name", "brand", "caliber", "business_unit", "supplier", "channel"]
SERIES_LIMIT = 12
SUM_METRICS = ["quantity", "amount_net", "amount_final", "amount_net_internal"]

ARTICLE_DEFAULTS = {
    "family": "Sin familia",
    "line": "Sin línea",
    "brand": "Sin marca",
    "caliber": "Sin calibre",
    "business_unit": "Sin unidad de negocio",
    "supplier": "Sin proveedor",
}
ARTICLE_COLUMNS = ["product_key", "product_name", *ARTICLE_DEFAULTS.keys(), "version"]

# Expresión SQL de cada campo sobre la venta (s) cruzada con el artículo (a).
# Un artículo que no está en el maestro queda con product_key vacío en el JOIN.
FIELD_EXPRESSIONS = {
    **{field: f"if(a.product_key = '', '{default}', a.{field})" for field, default in ARTICLE_DEFAULTS.items()},
    "product_name": "if(a.product_key = '', 'Sin producto', a.product_name)",
    "channel": "if(s.channel = '', 'Sin canal', s.channel)",
    "year": "toString(toYear(s.date))",
    "month": "toString(toMonth(s.date))",
}
# client_canonical de bi/facts: nombre (o clave) en minúsculas, sin acentos y
# con los espacios colapsados.
CLIENT_EXPRESSION = (
    "replaceRegexpAll(trimBoth(translateUTF8(lowerUTF8(if(s.client_name != '', s.client_name, s.client_key)),"
    " 'áéíóúüñ', 'aeiouun')), '\\\\s+', ' ')"
)


class SeriesUnavailable(RuntimeError):
    """El pedido no se puede resolver con totales (lo resuelve el informe completo)."""


def _string_list(values):
    return [str(value) for value in values]


def _shift_year(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # 29 de febrero
        return value.replace(year=value.year + years, day=28)


class SeriesQuery:
    def __init__(self, client, fact_table: str, dim_table: str):
        self.client = client
        self.fact_table = fact_table
        self.dim_table = dim_table

    def _base(self, start: date, end: date, filters: dict, exclude_field: str | None = None):
        conditions = ["s.date >= {start:Date}", "s.date <= {end:Date}"]
        parameters = {"start": start, "end": end}
        for field, values in filters.items():
            if field == exclude_field:
                continue
            name = f"f_{field}"
            conditions.append(f"{FIELD_EXPRESSIONS[field]} IN {{{name}:Array(String)}}")
            parameters[name] = _string_list(values)
        source = (
            f"FROM {self.fact_table} AS s "
            f"LEFT ANY JOIN {self.dim_table} AS a ON a.product_key = s.product_key "
            f"WHERE {' AND '.join(conditions)}"
        )
        return source, parameters

    def _rows(self, sql: str, parameters: dict):
        return self.client.query(sql, parameters=parameters).result_rows

    def totals_by_month(self, start, end, filters):
        source, parameters = self._base(start, end, filters)
        sql = (
            "SELECT formatDateTime(s.date, '%Y-%m') AS period, "
            "sum(s.quantity), sum(s.amount_net), sum(s.amount_final), sum(s.amount_net_internal), "
            f"uniqExact({CLIENT_EXPRESSION}), uniqExactIf(s.invoice, s.invoice != ''), count() "
            f"{source} GROUP BY period ORDER BY period"
        )
        return self._rows(sql, parameters)

    def period_totals(self, start, end, filters):
        source, parameters = self._base(start, end, filters)
        sql = (
            "SELECT sum(s.quantity), sum(s.amount_net), sum(s.amount_final), sum(s.amount_net_internal), "
            f"uniqExact({CLIENT_EXPRESSION}), uniqExactIf(s.invoice, s.invoice != ''), count() {source}"
        )
        rows = self._rows(sql, parameters)
        return rows[0] if rows else (0, 0, 0, 0, 0, 0, 0)

    def _projected(self, start, end, filters, fields, exclude_field=None):
        """Subconsulta con las ventas filtradas y una columna por campo.

        ClickHouse exige ARRAY JOIN antes del WHERE; filtrando en una subconsulta,
        la consulta de afuera abre cada fila en un par (campo, valor) por dimensión.
        """
        source, parameters = self._base(start, end, filters, exclude_field=exclude_field)
        columns = ", ".join(f"{FIELD_EXPRESSIONS[field]} AS d_{field}" for field in fields)
        inner = (
            f"SELECT {columns}, formatDateTime(s.date, '%Y-%m') AS period, "
            "s.quantity AS quantity, s.amount_net AS amount_net, s.amount_final AS amount_final, "
            f"s.amount_net_internal AS amount_net_internal, {CLIENT_EXPRESSION} AS client {source}"
        )
        pairs = "[" + ", ".join(f"('{field}', d_{field})" for field in fields) + "]"
        return f"FROM ({inner}) ARRAY JOIN {pairs} AS pair", parameters

    def series_by_dimension(self, start, end, filters, with_period: bool):
        source, parameters = self._projected(start, end, filters, SERIES_DIMENSIONS)
        period_select = "period, " if with_period else ""
        period_group = ", period" if with_period else ""
        sql = (
            f"SELECT pair.1 AS field, pair.2 AS value, {period_select}"
            "sum(quantity), sum(amount_net), sum(amount_final), sum(amount_net_internal), uniqExact(client) "
            f"{source} GROUP BY field, value{period_group}"
        )
        return self._rows(sql, parameters)

    def facet_counts(self, start, end, filters):
        """Opciones de cada filtro con los demás filtros aplicados (como build_faceted_available_filters).

        Los campos que no están filtrados comparten el mismo WHERE y van en una sola
        consulta; cada campo filtrado necesita la suya (sin su propio filtro).
        """
        groups = {None: [field for field in SERIES_FILTER_FIELDS if field not in filters]}
        for field in SERIES_FILTER_FIELDS:
            if field in filters:
                groups[field] = [field]
        counts = {}
        for excluded, fields in groups.items():
            if not fields:
                continue
            source, parameters = self._projected(start, end, filters, fields, exclude_field=excluded)
            sql = f"SELECT pair.1 AS field, pair.2 AS value, count() {source} GROUP BY field, value"
            for field, value, count in self._rows(sql, parameters):
                counts.setdefault(field, {})[value] = int(count)
        return counts


    # --- Ficha producto e Histórico ------------------------------------------

    def _window(self, start, end, previous, filters, fields):
        """Ventas del período (is_cur) y de la comparación (is_prev) con una columna por campo."""
        window_start = min(start, previous[0]) if previous else start
        window_end = max(end, previous[1]) if previous else end
        source, parameters = self._base(window_start, window_end, filters)
        parameters.update({"cur_start": start, "cur_end": end})
        previous_flag = "0"
        if previous:
            parameters.update({"prev_start": previous[0], "prev_end": previous[1]})
            previous_flag = "(s.date >= {prev_start:Date} AND s.date <= {prev_end:Date})"
        columns = "".join(f"{FIELD_EXPRESSIONS[field]} AS d_{field}, " for field in fields)
        inner = (
            f"SELECT {columns}formatDateTime(s.date, '%Y-%m') AS period, "
            f"{CLIENT_EXPRESSION} AS client, if(s.client_name != '', s.client_name, s.client_key) AS client_label, "
            "s.invoice AS invoice, s.amount_net AS net, s.quantity AS quantity, "
            "(s.date >= {cur_start:Date} AND s.date <= {cur_end:Date}) AS is_cur, "
            f"{previous_flag} AS is_prev {source}"
        )
        return f"FROM ({inner}) WHERE (is_cur OR is_prev)", parameters

    PERIOD_SUMS = (
        "sumIf(net, is_cur), sumIf(net, is_prev), sumIf(quantity, is_cur), sumIf(quantity, is_prev), "
        "uniqExactIf(client, is_cur), uniqExactIf(client, is_prev)"
    )

    def product_groups(self, start, end, previous, filters, group):
        source, parameters = self._window(start, end, previous, filters, [group])
        sql = (
            f"SELECT d_{group}, {self.PERIOD_SUMS}, uniqExactIf(invoice, is_cur AND invoice != '') "
            f"{source} GROUP BY d_{group}"
        )
        return self._rows(sql, parameters)

    def product_client_changes(self, start, end, previous, filters, group):
        source, parameters = self._window(start, end, previous, filters, [group])
        sql = (
            f"SELECT d_{group}, countIf(bought_now AND NOT bought_before), countIf(bought_before AND NOT bought_now) "
            f"FROM (SELECT d_{group}, client, max(is_cur) AS bought_now, max(is_prev) AS bought_before "
            f"{source} GROUP BY d_{group}, client) GROUP BY d_{group}"
        )
        return self._rows(sql, parameters)

    def product_children(self, start, end, previous, filters, group, child):
        source, parameters = self._window(start, end, previous, filters, [group, child])
        sql = f"SELECT d_{group}, d_{child}, {self.PERIOD_SUMS} {source} GROUP BY d_{group}, d_{child}"
        return self._rows(sql, parameters)

    def product_top_clients(self, start, end, previous, filters, group, limit=10):
        source, parameters = self._window(start, end, previous, filters, [group])
        sql = (
            f"SELECT d_{group}, client, any(client_label), sumIf(net, is_cur) AS current_sales, "
            "sumIf(net, is_prev) AS previous_sales, sumIf(quantity, is_cur), sumIf(quantity, is_prev), "
            "uniqExactIf(client, is_cur), uniqExactIf(client, is_prev) "
            f"{source} GROUP BY d_{group}, client HAVING current_sales > 0 "
            f"ORDER BY d_{group}, current_sales DESC, previous_sales DESC LIMIT {int(limit)} BY d_{group}"
        )
        return self._rows(sql, parameters)

    def window_totals(self, start, end, previous, filters):
        source, parameters = self._window(start, end, previous, filters, [])
        return self._rows(f"SELECT {self.PERIOD_SUMS} {source}", parameters)[0]

    def clients_by_month(self, start, end, filters):
        source, parameters = self._window(start, end, None, filters, [])
        return self._rows(f"SELECT period, groupUniqArray(client) {source} GROUP BY period ORDER BY period", parameters)

    def sales_by_client_label(self, start, end, filters):
        source, parameters = self._window(start, end, None, filters, [])
        return self._rows(f"SELECT client_label, sum(net) {source} GROUP BY client_label", parameters)

    def budget_rows(self, start, end, filters, periods):
        source, parameters = self._window(start, end, None, filters, ["line", "channel", "family"])
        parameters["budget_periods"] = list(periods)
        sql = (
            "SELECT period, d_line, d_channel, d_family, sum(net), sum(quantity) "
            f"{source} AND period IN {{budget_periods:Array(String)}} GROUP BY period, d_line, d_channel, d_family"
        )
        return self._rows(sql, parameters)


def normalize_series_filters(raw_filters):
    applied = {}
    for field, values in (raw_filters or {}).items():
        if field not in FILTER_DEFINITIONS:
            continue
        if field not in SERIES_FILTER_FIELDS:
            raise SeriesUnavailable(f"El filtro {field} se resuelve con el informe completo")
        normalized = normalize_filter_values(field, values)
        if not normalized:
            raise SeriesUnavailable("Un filtro quedó sin valores")
        applied[field] = normalized
    return applied


def _metric_tuple(row):
    return {metric: float(value or 0) for metric, value in zip(SUM_METRICS, row)}


def build_series_report(query: SeriesQuery, start: date, end: date, raw_filters, compare: bool = False):
    if start > end:
        raise ValueError("fechaDesde no puede ser mayor que fechaHasta")
    filters = normalize_series_filters(raw_filters)
    labels = month_labels_between(start, end)

    monthly = {row[0]: row for row in query.totals_by_month(start, end, filters)}
    period = query.period_totals(start, end, filters)
    rows_analyzed = int(period[6] or 0)
    if not rows_analyzed:
        raise ValueError("No quedaron ventas para los filtros seleccionados")

    period_by_value = {}
    for field, value, *metrics in query.series_by_dimension(start, end, filters, with_period=False):
        period_by_value.setdefault(field, {})[value] = metrics
    monthly_by_value = {}
    for field, value, label, *metrics in query.series_by_dimension(start, end, filters, with_period=True):
        monthly_by_value.setdefault(field, {}).setdefault(value, {})[label] = metrics

    metrics = [
        {"key": key, **config}
        for key, config in INTERACTIVE_REPORT_METRICS.items()
        if key != "quantity" or float(period[0] or 0)
    ]
    metrics.append(dict(INTERACTIVE_REPORT_BUYERS_METRIC))
    metric_index = {key: index for index, key in enumerate(SUM_METRICS)}
    metric_index["buyers"] = len(SUM_METRICS)

    # period: (q, neto, final, neto+int, compradores, pedidos, filas); monthly: (mes, ...mismo orden).
    totals = []
    for metric in metrics:
        key = metric["key"]
        index = metric_index[key]
        series = [{"label": label, "value": round(float((monthly.get(label) or [0] * 8)[index + 1] or 0), 2)} for label in labels]
        total = int(period[index] or 0) if key == "buyers" else round(float(period[index] or 0), 2)
        totals.append({"metric": key, "total": total, "series": series})

    by_dimension = {}
    dimensions = []
    for field in SERIES_DIMENSIONS:
        values = period_by_value.get(field) or {}
        if not values:
            continue
        dimensions.append({"key": field, "label": FILTER_DEFINITIONS.get(field, {}).get("label", field)})
        by_dimension[field] = {}
        for metric in metrics:
            key = metric["key"]
            index = metric_index[key]
            ranked = sorted(values.items(), key=lambda pair: float(pair[1][index] or 0), reverse=True)[:SERIES_LIMIT]
            by_dimension[field][key] = [
                {
                    "name": name,
                    "total": int(period_metrics[index] or 0) if key == "buyers" else round(float(period_metrics[index] or 0), 2),
                    "data": [
                        (int if key == "buyers" else float)(round(float((monthly_by_value[field][name].get(label) or [0] * 5)[index] or 0), 2))
                        for label in labels
                    ],
                }
                for name, period_metrics in ranked
            ]

    available_filters = _available_filters(query.facet_counts(start, end, filters), filters, start, end)
    summary = _summary(period, period_by_value, labels, start, end)
    comparison = None
    if compare:
        previous_start, previous_end = _shift_year(start, -1), _shift_year(end, -1)
        previous = query.period_totals(previous_start, previous_end, filters)
        comparison = {
            "comparisonStart": previous_start.isoformat(),
            "comparisonEnd": previous_end.isoformat(),
            "comparisonLabel": f"{previous_start.strftime('%d/%m/%Y')} al {previous_end.strftime('%d/%m/%Y')}",
        }
        summary.update(_comparison_summary(summary, previous))

    return {
        "kind": "series",
        "meta": {
            "periodStart": start.isoformat(),
            "periodEnd": end.isoformat(),
            "rowsAnalyzed": rows_analyzed,
            "rowsUniverse": rows_analyzed,
            "activeFilterSummary": summarize_filters(filters),
            "source": "clickhouse",
            "comparison": comparison,
        },
        "summary": summary,
        "interactiveReport": {
            "labels": labels,
            "metrics": metrics,
            "dimensions": dimensions,
            "totals": totals,
            "byDimension": by_dimension,
        },
        "availableFilters": available_filters,
        "appliedFilters": filters,
    }


def _available_filters(counts, filters, start, end):
    available = {}
    for field in SERIES_FILTER_FIELDS:
        meta = FILTER_DEFINITIONS[field]
        field_counts = {}
        for value, count in (counts.get(field) or {}).items():
            parsed = int(value) if field in {"year", "month"} else value
            field_counts[parsed] = field_counts.get(parsed, 0) + count
        if field in {"year", "month"}:
            for value in period_filter_values(field, start, end):
                field_counts.setdefault(value, 0)
        if not field_counts:
            continue
        available[field] = {
            "label": meta["label"],
            "kind": meta["kind"],
            "options": [
                {"value": value, "label": filter_label(field, value), "count": field_counts[value]}
                for value in sort_filter_values(field, field_counts.keys())
            ],
            "constrainedBy": sorted(other for other in filters if other != field),
        }
    return available


def _real_count(values, field):
    default = ARTICLE_DEFAULTS.get(field) or {"channel": "Sin canal", "product_name": "Sin producto"}.get(field)
    return sum(1 for value in values if value != default)


def _summary(period, period_by_value, labels, start, end):
    units, sales, sales_final, _, buyers, orders, _ = (float(value or 0) for value in period)
    return {
        "periodLabel": f"{start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}",
        "monthsCovered": len(labels),
        "salesCurrent": round(sales, 2),
        "salesFinalCurrent": round(sales_final, 2),
        "unitsCurrent": round(units, 2),
        "ordersCurrent": int(orders),
        "activeClients": int(buyers),
        "avgOrderValue": round(sales / orders, 2) if orders else 0,
        "avgUnitPrice": round(sales / units, 2) if units else 0,
        "avgUnitsPerOrder": round(units / orders, 2) if orders else 0,
        "familyCount": _real_count(period_by_value.get("family") or {}, "family"),
        "brandCount": _real_count(period_by_value.get("brand") or {}, "brand"),
        "channelCount": _real_count(period_by_value.get("channel") or {}, "channel"),
        "supplierCount": _real_count(period_by_value.get("supplier") or {}, "supplier"),
        "volumeModeActive": units > 0,
    }


def _pct_change(current, previous):
    if not previous:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def _comparison_summary(summary, previous):
    units, sales, _, _, buyers, orders, _ = (float(value or 0) for value in previous)
    return {
        "salesPrevious": round(sales, 2),
        "unitsPrevious": round(units, 2),
        "ordersPrevious": int(orders),
        "activeClientsPrevious": int(buyers),
        "salesGrowthPct": _pct_change(summary["salesCurrent"], sales),
        "unitsGrowthPct": _pct_change(summary["unitsCurrent"], units),
        "activeClientsGrowthPct": _pct_change(summary["activeClients"], buyers),
    }


# --- Maestro de artículos en ClickHouse ---------------------------------------

_dim_lock = Lock()
_dim_loaded_version = None


def article_dimension_rows(articles, version):
    rows = {}
    for article in articles or []:
        product_key = str(article.get("product_key") or "").strip()
        if not product_key:
            continue
        rows[product_key] = [
            product_key,
            str(article.get("product_name") or "").strip() or product_key,
            *[str(article.get(field) or "").strip() or default for field, default in ARTICLE_DEFAULTS.items()],
            str(version or ""),
        ]
    return list(rows.values())


def ensure_article_dimension(client, database: str, version, load_articles):
    """Copia el maestro de artículos a ClickHouse cuando cambia su versión.

    Se arma en una tabla auxiliar y se intercambia de una sola vez, así ninguna
    consulta ve la dimensión vacía o a medio cargar.
    """
    global _dim_loaded_version
    if version is not None and version == _dim_loaded_version:
        return
    with _dim_lock:
        if version is not None and version == _dim_loaded_version:
            return
        table = f"{database}.dim_articles"
        staging = f"{database}.dim_articles_staging"
        columns = ", ".join(f"{column} String" for column in ARTICLE_COLUMNS)
        client.command(f"CREATE TABLE IF NOT EXISTS {table} ({columns}) ENGINE = MergeTree ORDER BY product_key")
        stored_version, stored_count = client.query(f"SELECT any(version), count() FROM {table}").result_rows[0]
        if stored_count and (version is None or stored_version == str(version)):
            _dim_loaded_version = version if version is not None else stored_version
            return
        rows = article_dimension_rows(load_articles(), version)
        if not rows:
            if stored_count:
                return
            raise SeriesUnavailable("No hay maestro de artículos para cruzar las ventas")
        client.command(f"CREATE TABLE IF NOT EXISTS {staging} AS {table}")
        client.command(f"TRUNCATE TABLE {staging}")
        client.insert(staging, rows, column_names=ARTICLE_COLUMNS)
        client.command(f"EXCHANGE TABLES {table} AND {staging}")
        client.command(f"TRUNCATE TABLE {staging}")
        _dim_loaded_version = version


# --- Ficha producto e Histórico con totales -----------------------------------

COMPARISON_FIELDS = (
    "previousSales", "growthPct", "previousQuantity", "quantityGrowthPct", "previousClients",
    "clientsGrowthPct", "clientDelta", "newClients", "clientsWithoutPurchase",
)


def _without_comparison(value):
    """Sin período de comparación pedido, las variaciones no existen: quedan en None."""
    if isinstance(value, list):
        return [_without_comparison(item) for item in value]
    if isinstance(value, dict):
        return {key: None if key in COMPARISON_FIELDS else _without_comparison(item) for key, item in value.items()}
    return value


def _sums(row):
    sales, previous_sales, quantity, previous_quantity, clients, previous_clients = row
    return (
        {"sales": float(sales or 0), "quantity": float(quantity or 0), "clients": int(clients or 0)},
        {"sales": float(previous_sales or 0), "quantity": float(previous_quantity or 0), "clients": int(previous_clients or 0)},
    )


def product_parts_from_query(query: SeriesQuery, start, end, previous, filters, group, child):
    groups = {}
    for label, *metrics, orders in query.product_groups(start, end, previous, filters, group):
        now, before = _sums(metrics)
        groups[label] = {
            "label": label,
            "sales": round(now["sales"], 2),
            "previousSales": round(before["sales"], 2),
            "quantity": round(now["quantity"], 2),
            "previousQuantity": round(before["quantity"], 2),
            "clients": now["clients"],
            "previousClients": before["clients"],
            "newClients": 0,
            "clientsWithoutPurchase": 0,
            "orders": int(orders or 0),
            "children": {"current": {}, "previous": {}, "names": {}},
            "topClients": {"current": {}, "previous": {}, "names": {}},
        }
    for label, new_clients, lost_clients in query.product_client_changes(start, end, previous, filters, group):
        if label in groups:
            groups[label]["newClients"] = int(new_clients or 0)
            groups[label]["clientsWithoutPurchase"] = int(lost_clients or 0)
    for label, child_label, *metrics in query.product_children(start, end, previous, filters, group, child):
        if label not in groups:
            continue
        now, before = _sums(metrics)
        block = groups[label]["children"]
        if now["sales"] or now["quantity"] or now["clients"]:
            block["current"][child_label] = now
        if before["sales"] or before["quantity"] or before["clients"]:
            block["previous"][child_label] = before
    for label, client, client_label, *metrics in query.product_top_clients(start, end, previous, filters, group):
        if label not in groups:
            continue
        now, before = _sums(metrics)
        block = groups[label]["topClients"]
        block["current"][client] = now
        if before["sales"] or before["quantity"] or before["clients"]:
            block["previous"][client] = before
        block["names"][client] = client_label or client
    now, before = _sums(query.window_totals(start, end, previous, filters))
    return {
        "groups": list(groups.values()),
        "sales": round(now["sales"], 2),
        "quantity": round(now["quantity"], 2),
        "clients": now["clients"],
        "previousSales": round(before["sales"], 2),
        "previousQuantity": round(before["quantity"], 2),
        "previousClients": before["clients"],
    }


def build_product_dashboards(query: SeriesQuery, start, end, raw_filters, previous=None):
    """Fichas por familia (con categorías) y por categoría (con marcas), sin vendedores:
    en Comercial la asignación de vendedores no aplica y se ve en Sales Coach."""
    from bi.focus_dashboards import build_product_dashboard_from_parts

    filters = normalize_series_filters(raw_filters)
    dashboards = {}
    for key, group, child in (("families", "family", "line"), ("lines", "line", "brand")):
        dashboard = build_product_dashboard_from_parts(
            group, child, product_parts_from_query(query, start, end, previous, filters, group, child)
        )
        dashboard["comparison"] = bool(previous)
        dashboards[key] = dashboard if previous else _without_comparison(dashboard)
    return dashboards


def history_parts_from_query(query: SeriesQuery, start, end, filters):
    monthly_rows = [
        {
            "label": label,
            "sales": round(float(net or 0), 2),
            "quantity": round(float(quantity or 0), 2),
            "clients": int(buyers or 0),
            "orders": int(orders or 0),
        }
        for label, quantity, net, _final, _internal, buyers, orders, _count in query.totals_by_month(start, end, filters)
    ]
    period = query.period_totals(start, end, filters)
    dimension_sums = {field: {} for field in ("supplier", "channel", "family")}
    for field, value, _quantity, net, *_ in query.series_by_dimension(start, end, filters, with_period=False):
        if field in dimension_sums:
            dimension_sums[field][value] = float(net or 0)
    client_sums = {label or "Sin cliente": float(net or 0) for label, net in query.sales_by_client_label(start, end, filters)}
    dimension_sums["client"] = client_sums
    budget_periods = [row["label"] for row in monthly_rows][-3:]
    budget_records = {label: [] for label in budget_periods}
    if budget_periods:
        for label, line, channel, family, net, quantity in query.budget_rows(start, end, filters, budget_periods):
            budget_records[label].append(
                # Sin vendedor: el reparto del objetivo por vendedor es de Sales Coach.
                {"line": line, "channel": channel, "family": family, "seller_name": "Sin vendedor",
                 "amount": float(net or 0), "quantity": float(quantity or 0)}
            )
    return {
        "monthly_rows": monthly_rows,
        "clients_by_period": {label: set(clients) for label, clients in query.clients_by_month(start, end, filters)},
        "current_total": round(float(period[1] or 0), 2),
        "pareto_sums": client_sums,
        "dimension_sums": dimension_sums,
        "budget_records_by_period": budget_records,
    }


def build_history_dashboards(query: SeriesQuery, start, end, raw_filters, planning=None):
    from bi.structural_dashboards import build_history_from_parts

    filters = normalize_series_filters(raw_filters)
    return {"history": build_history_from_parts(history_parts_from_query(query, start, end, filters), planning=planning)}
