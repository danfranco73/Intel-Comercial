"""
viz_selector.py — Fase 4b del motor dinámico de análisis comercial.

Responsabilidad:
    Dado un resultado de AnalysisEngine (y su task), producir una
    especificación de visualización lista para que el frontend la renderice
    sin lógica adicional.

    No depende de nombres de columna ni de Chart.js / D3 / librería específica.
    Devuelve una estructura portátil que el frontend interpreta.

Estructura de salida — VizSpec:
    {
        "type":    "line" | "bar" | "bar_horizontal" | "donut" |
                   "stacked_bar" | "scatter" | "heatmap" | "area",
        "title":   str,
        "series":  [Series],     ← datos normalizados
        "axes":    {x, y},       ← etiquetas de eje
        "format":  "money" | "pct" | "int" | "text",
        "options": {dict},       ← configuración adicional
        "empty":   bool,
    }

    Series = {
        "id":     str,
        "label":  str,
        "data":   [{x, y, z?}],  ← z para scatter/heatmap
        "color":  str | None,
    }

Uso:
    from viz_selector import build_viz
    spec = build_viz(task, engine_result)
"""

from __future__ import annotations


# Paleta de colores corporativa (consistente con app.css --brand)
_PALETTE = [
    "#0f766e",  # brand teal
    "#1d4ed8",  # blue
    "#b45309",  # amber
    "#7c3aed",  # violet
    "#be185d",  # pink
    "#15803d",  # green
    "#b91c1c",  # red
    "#0369a1",  # sky
]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def build_viz(task: dict, result: dict) -> dict:
    """
    Construye la VizSpec para un AnalysisTask y su resultado de engine.

    Args:
        task:   AnalysisTask (de rule_engine)
        result: result_dict  (de analysis_engine)

    Returns:
        VizSpec
    """
    if "error" in result:
        return _empty_spec(task.get("label", task["id"]))

    builder = _BUILDERS.get(task["id"])
    if builder is None:
        return _empty_spec(task.get("label", task["id"]))

    return builder(task, result)


def build_all_viz(tasks: list, engine_results: dict) -> dict:
    """
    Construye todas las VizSpecs para el conjunto de tasks.

    Returns:
        dict[task_id → VizSpec]
    """
    task_map = {t["id"]: t for t in tasks}
    return {
        task_id: build_viz(task_map[task_id], result)
        for task_id, result in engine_results.items()
        if task_id in task_map
    }


# ---------------------------------------------------------------------------
# Builders por tipo de análisis
# ---------------------------------------------------------------------------

def _viz_temporal(task, result):
    series_data = result.get("series", [])
    forecast    = result.get("forecast", [])
    by_dim      = result.get("by_dim", {})
    trend       = result.get("trend_pct", 0.0)

    if not series_data:
        return _empty_spec(task.get("label", "Evolución temporal"))

    if by_dim:
        main_series = []
        for i, (dim_val, dim_series) in enumerate(list(by_dim.items())[:4]):
            main_series.append(_serie(
                f"dim_{i}", dim_val,
                [{"x": p["label"], "y": p["value"]} for p in dim_series],
                _PALETTE[i % len(_PALETTE)],
            ))
    else:
        main_series = [_serie("total", "Total", [{**p, "x": p["label"], "y": p["value"]} for p in series_data], _PALETTE[0])]
        if forecast:
            main_series.append(_serie("forecast", "Proyección",
                                      [{"x": p["label"], "y": p["value"]} for p in forecast],
                                      _PALETTE[2], dashed=True))

    return {
        "type":   "line",
        "title":  task.get("label", "Evolución temporal"),
        "series": main_series,
        "axes":   {"x": "Período", "y": result.get("metric_field", "Valor")},
        "format": "money",
        "options": {
            "show_forecast": bool(forecast),
            "trend_pct":     trend,
            "by_dim":        bool(by_dim),
        },
        "empty": False,
    }


def _viz_ranking(task, result):
    items  = result.get("items", [])
    total  = result.get("total", 1)
    dim    = result.get("dimension_field", "dimensión")

    if not items:
        return _empty_spec(task.get("label", "Ranking"))

    # Donut si ≤ 6 items, bar_horizontal si > 6
    viz_type = "donut" if len(items) <= 6 else "bar_horizontal"

    data = [{"x": item["label"], "y": item["value"], "z": item["share_pct"]}
            for item in items[:12]]
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(data))]

    series = [_serie("ranking", dim, data)]
    if viz_type == "donut":
        series[0]["colors"] = colors

    return {
        "type":   viz_type,
        "title":  f"{task.get('label','Ranking')} — {dim}",
        "series": series,
        "axes":   {"x": dim, "y": result.get("metric_field", "Valor")},
        "format": "money",
        "options": {
            "show_share":   True,
            "top3_pct":     result.get("concentration_top3_pct", 0),
        },
        "empty": False,
    }


def _viz_churn(task, result):
    by_status    = result.get("by_status", {})
    status_sales = result.get("status_sales", {})

    if not by_status:
        return _empty_spec(task.get("label", "Recurrencia"))

    # Stacked bar: clientes por estado + venta por estado
    status_order = ["Activo", "Dormido", "Reactivable", "Perdido"]
    status_colors = {"Activo": "#15803d", "Dormido": "#b45309",
                     "Reactivable": "#1d4ed8", "Perdido": "#b91c1c"}

    count_series = [
        _serie(s.lower(), s,
               [{"x": s, "y": by_status.get(s, 0)}],
               status_colors.get(s, _PALETTE[0]))
        for s in status_order if s in by_status
    ]
    sales_series = [
        _serie(f"{s.lower()}_sales", f"{s} (venta)",
               [{"x": s, "y": status_sales.get(s, 0)}],
               status_colors.get(s, _PALETTE[0]))
        for s in status_order if s in status_sales
    ]

    return {
        "type":   "stacked_bar",
        "title":  task.get("label", "Estado de cartera"),
        "series": count_series,
        "series_secondary": sales_series,
        "axes":  {"x": "Estado", "y": "Clientes"},
        "format": "int",
        "options": {
            "active_pct":    result.get("active_pct", 0),
            "recurring_pct": result.get("recurring_pct", 0),
        },
        "empty": False,
    }


def _viz_cross_sell(task, result):
    dist = result.get("breadth_distribution", {})

    if not dist:
        return _empty_spec(task.get("label", "Mix"))

    data = [{"x": str(k), "y": v} for k, v in sorted(dist.items())]
    series = [_serie("breadth_dist", "Clientes por N° de categorías", data, _PALETTE[0])]

    return {
        "type":   "bar",
        "title":  task.get("label", "Amplitud de surtido"),
        "series": series,
        "axes":   {"x": "Familias compradas", "y": "Clientes"},
        "format": "int",
        "options": {
            "avg_breadth":         result.get("avg_breadth", 0),
            "low_breadth_clients": result.get("low_breadth_clients", 0),
        },
        "empty": False,
    }


def _viz_seller(task, result):
    sellers = result.get("sellers", [])

    if not sellers:
        return _empty_spec(task.get("label", "Vendedores"))

    data = [{"x": s["seller"], "y": s["value"], "z": s.get("growth_pct", 0)}
            for s in sellers[:12]]
    colors = [_growth_color(s.get("growth_pct", 0)) for s in sellers[:12]]

    series = [_serie("sellers", result.get("seller_field", "vendedor"), data)]
    series[0]["colors"] = colors

    return {
        "type":   "bar_horizontal",
        "title":  task.get("label", "Performance vendedores"),
        "series": series,
        "axes":   {"x": "Venta", "y": result.get("seller_field", "Vendedor")},
        "format": "money",
        "options": {
            "show_growth":    True,
            "top3_share_pct": result.get("concentration_top3_pct", 0),
        },
        "empty": False,
    }


def _viz_geographic(task, result):
    routes = result.get("routes", [])

    if not routes:
        return _empty_spec(task.get("label", "Cobertura"))

    data   = [{"x": r["route"], "y": r["value"], "z": r["covered"]} for r in routes[:15]]
    colors = [_PALETTE[0] if r["covered"] else "#b91c1c" for r in routes[:15]]

    series = [_serie("routes", "Ruta", data)]
    series[0]["colors"] = colors

    return {
        "type":   "bar_horizontal",
        "title":  task.get("label", "Cobertura geográfica"),
        "series": series,
        "axes":   {"x": "Venta", "y": "Ruta"},
        "format": "money",
        "options": {
            "uncovered_pct":   result.get("uncovered_pct", 0),
            "uncovered_value": result.get("uncovered_value", 0),
        },
        "empty": False,
    }


def _viz_margin(task, result):
    items = result.get("items", [])

    if not items:
        return _empty_spec(task.get("label", "Margen"))

    # Scatter: x=revenue, y=margin_pct, z=label
    scatter_data = [
        {"x": item["revenue"], "y": item["margin_pct"], "z": item["label"]}
        for item in items[:20]
    ]
    colors = [_margin_color(item["margin_pct"]) for item in items[:20]]

    series = [_serie("margin_scatter", "Venta vs Margen %", scatter_data)]
    series[0]["colors"] = colors

    return {
        "type":   "scatter",
        "title":  task.get("label", "Análisis de margen"),
        "series": series,
        "axes":   {"x": "Facturación", "y": "Margen %"},
        "format": "pct",
        "options": {
            "total_margin_pct": result.get("total_margin_pct", 0),
        },
        "empty": False,
    }


def _viz_sales_force(task, result):
    forces = result.get("forces", result.get("sellers", []))

    if not forces:
        return _empty_spec(task.get("label", "Fuerza de ventas"))

    data   = [{"x": f.get("seller", f.get("force", "-")), "y": f["value"]} for f in forces[:8]]
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(data))]

    series = [_serie("forces", "Fuerza de ventas", data)]
    series[0]["colors"] = colors

    return {
        "type":   "bar",
        "title":  task.get("label", "Desglose fuerza de ventas"),
        "series": series,
        "axes":   {"x": "Fuerza de ventas", "y": "Venta"},
        "format": "money",
        "options": {},
        "empty": False,
    }


def _viz_product(task, result):
    items = result.get("items", [])

    if not items:
        return _empty_spec(task.get("label", "Producto"))

    # Dos series: participación (bar) + momentum (diverging bar)
    share_data    = [{"x": i["label"], "y": i["value"],      "z": i["share_pct"]} for i in items[:10]]
    momentum_data = [{"x": i["label"], "y": i.get("growth_pct", 0)} for i in items[:10]]
    momentum_colors = [_growth_color(i.get("growth_pct", 0)) for i in items[:10]]

    series = [
        _serie("share",    "Participación", share_data,    _PALETTE[0]),
        _serie("momentum", "Crecimiento %", momentum_data, _PALETTE[1]),
    ]
    series[1]["colors"]   = momentum_colors
    series[1]["diverging"] = True

    return {
        "type":   "bar",
        "title":  task.get("label", "Análisis de producto"),
        "series": series,
        "axes":   {"x": result.get("product_field", "Categoría"), "y": "Venta"},
        "format": "money",
        "options": {
            "show_momentum": True,
            "top_category":  result.get("top_category"),
        },
        "empty": False,
    }


def _viz_channel(task, result):
    items = result.get("items", [])

    if not items:
        return _empty_spec(task.get("label", "Canal"))

    data   = [{"x": i["label"], "y": i["value"], "z": i["share_pct"]} for i in items]
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(data))]

    # Donut si ≤ 5 canales
    viz_type = "donut" if len(items) <= 5 else "bar"
    series = [_serie("channels", "Canal", data)]
    series[0]["colors"] = colors

    return {
        "type":   viz_type,
        "title":  task.get("label", "Análisis por canal"),
        "series": series,
        "axes":   {"x": "Canal", "y": "Venta"},
        "format": "money",
        "options": {
            "top_channel": result.get("top_channel"),
        },
        "empty": False,
    }


# Registro de builders
_BUILDERS = {
    "temporal_trend":        _viz_temporal,
    "dimension_ranking":     _viz_ranking,
    "recurrence_churn":      _viz_churn,
    "cross_sell_mix":        _viz_cross_sell,
    "seller_performance":    _viz_seller,
    "geographic_coverage":   _viz_geographic,
    "margin_analysis":       _viz_margin,
    "sales_force_breakdown": _viz_sales_force,
    "product_analysis":      _viz_product,
    "channel_analysis":      _viz_channel,
}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _serie(sid, label, data, color=None, dashed=False):
    s = {"id": sid, "label": label, "data": data}
    if color:
        s["color"] = color
    if dashed:
        s["dashed"] = True
    return s


def _empty_spec(title):
    return {
        "type": "bar", "title": title, "series": [],
        "axes": {"x": "", "y": ""}, "format": "money",
        "options": {}, "empty": True,
    }


def _growth_color(pct):
    if pct >= 5:
        return "#15803d"   # verde
    if pct <= -10:
        return "#b91c1c"   # rojo
    return "#b45309"       # ámbar


def _margin_color(margin_pct):
    if margin_pct >= 20:
        return "#15803d"
    if margin_pct >= 10:
        return "#b45309"
    return "#b91c1c"
