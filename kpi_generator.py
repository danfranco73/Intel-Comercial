"""
kpi_generator.py — Fase 4a del motor dinámico de análisis comercial.

Responsabilidad:
    Tomar los resultados crudos del AnalysisEngine y calcular KPIs
    derivados listos para mostrar en la UI.

    Completamente agnóstico a nombres de columna: trabaja sobre las
    estructuras normalizadas que entrega el engine (task_id → result_dict).

Estructura de salida — KpiSet:
    {
        "kpis": [KpiCard],          ← cards individuales para la UI
        "semaphores": [Semaphore],  ← indicadores de color rojo/amarillo/verde
        "ratios": {str: float},     ← ratios derivados para insights
    }

    KpiCard = {
        "id":       str,
        "label":    str,
        "value":    float | int | str,
        "format":   "money" | "pct" | "int" | "text",
        "delta":    float | None,   ← variación vs período anterior (si aplica)
        "delta_dir": "up"|"down"|"flat",
        "context":  str,            ← texto corto de contexto
        "domain":   str,
        "priority": int,
    }

    Semaphore = {
        "id":     str,
        "label":  str,
        "color":  "green" | "yellow" | "red",
        "value":  float,
        "detail": str,
    }

Uso:
    from kpi_generator import generate_kpis
    kpi_set = generate_kpis(engine_results, tasks)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Umbrales por defecto (ajustables sin tocar el motor)
# ---------------------------------------------------------------------------

_THRESHOLDS = {
    "growth_pct":          (0.0,   8.0),   # rojo < 0, amarillo 0-8, verde > 8
    "active_pct":          (55.0,  75.0),
    "recurring_pct":       (40.0,  60.0),
    "concentration_top3":  (60.0,  45.0),  # invertido: verde < 45, rojo > 60
    "coverage_pct":        (80.0,  92.0),
    "churn_risk_pct":      (30.0,  15.0),  # invertido: verde < 15
    "margin_pct":          (10.0,  20.0),
    "avg_breadth":         (2.0,   3.0),
}


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def generate_kpis(engine_results: dict, tasks: list) -> dict:
    """
    Genera el KpiSet completo a partir de los resultados del AnalysisEngine.

    Args:
        engine_results: dict[task_id → result_dict]  (salida de engine.run_all)
        tasks:          list[AnalysisTask]            (salida de rule_engine)

    Returns:
        dict — KpiSet con "kpis", "semaphores" y "ratios"
    """
    kpis       = []
    semaphores = []
    ratios     = {}

    task_map = {t["id"]: t for t in tasks}

    for task_id, result in engine_results.items():
        if "error" in result:
            continue
        task = task_map.get(task_id, {})
        domain   = task.get("domain", "general")
        priority = task.get("priority", 2)

        extractor = _EXTRACTORS.get(task_id)
        if extractor is None:
            continue

        new_kpis, new_semaphores, new_ratios = extractor(result, domain, priority)
        kpis.extend(new_kpis)
        semaphores.extend(new_semaphores)
        ratios.update(new_ratios)

    # Ordenar kpis: prioridad, luego dominio
    _domain_order = ["tiempo", "cartera", "territorio", "producto", "canal", "margen", "general"]
    kpis.sort(key=lambda k: (k["priority"], _domain_order.index(k["domain"]) if k["domain"] in _domain_order else 99))
    semaphores.sort(key=lambda s: {"red": 0, "yellow": 1, "green": 2}.get(s["color"], 3))

    return {"kpis": kpis, "semaphores": semaphores, "ratios": ratios}


# ---------------------------------------------------------------------------
# Extractores por tipo de análisis
# ---------------------------------------------------------------------------

def _extract_temporal(result, domain, priority):
    kpis, sem, rat = [], [], {}

    trend   = result.get("trend_pct", 0.0)
    base    = result.get("last3_avg", 0.0)
    proj    = base * 3 * (1 + max(min(trend, 20), -20) * 0.6 / 100)
    periods = result.get("periods", 0)

    kpis.append(_kpi("trend_pct",  "Tendencia reciente",    trend,   "pct",   domain, priority,
                     delta=trend, context=f"Base mensual {_fmt_money(base)}"))
    kpis.append(_kpi("base_monthly", "Promedio mensual (últ. 3)", base, "money", domain, priority,
                     context=f"{periods} períodos analizados"))
    kpis.append(_kpi("projected_quarter", "Proyección próximo trimestre", proj, "money", domain, priority,
                     context="Basado en tendencia moderada"))

    rat["trend_pct"]   = trend
    rat["base_monthly"] = round(base, 2)

    sem.append(_semaphore("crecimiento", "Crecimiento", trend,
                          _thresholds("growth_pct"), f"Tendencia {_sign(trend)}{abs(trend)}%"))
    return kpis, sem, rat


def _extract_ranking(result, domain, priority):
    kpis, sem, rat = [], [], {}

    top3   = result.get("concentration_top3_pct", 0.0)
    hhi    = result.get("hhi", 0.0)
    total  = result.get("total", 0.0)
    dim    = result.get("dimension_field", "dimensión")
    n      = len(result.get("items", []))

    kpis.append(_kpi("dim_total",  f"Total ({dim})",    total,  "money", domain, priority,
                     context=f"{n} elementos"))
    kpis.append(_kpi("top3_share", "Concentración top 3", top3, "pct",   domain, priority,
                     context="Participación de los 3 primeros"))
    kpis.append(_kpi("hhi",        "Índice HHI",          hhi,  "int",   domain, priority,
                     context="<1000=fragmentado, >2500=concentrado"))

    rat["dim_concentration_pct"] = top3
    rat["hhi"]                   = hhi

    # HHI invertido como semáforo: < 1000 verde, 1000-2500 amarillo, > 2500 rojo
    color = "green" if hhi < 1000 else ("yellow" if hhi < 2500 else "red")
    sem.append({"id": "hhi", "label": "Concentración (HHI)", "color": color,
                "value": hhi, "detail": f"HHI={hhi:.0f} — {_hhi_label(hhi)}"})
    return kpis, sem, rat


def _extract_churn(result, domain, priority):
    kpis, sem, rat = [], [], {}

    total      = result.get("total_clients", 1)
    active_pct = result.get("active_pct", 0.0)
    rec_pct    = result.get("recurring_pct", 0.0)
    avg_t      = result.get("avg_ticket", 0.0)
    at_risk    = result.get("churn_at_risk_sales", 0.0)
    by_status  = result.get("by_status", {})

    dormant    = by_status.get("Dormido", 0)
    react      = by_status.get("Reactivable", 0)
    lost       = by_status.get("Perdido", 0)
    churn_pct  = round((dormant + react + lost) / max(total, 1) * 100, 1)

    kpis.append(_kpi("total_clients",  "Total clientes",      total,      "int",   domain, priority))
    kpis.append(_kpi("active_pct",     "Clientes activos",    active_pct, "pct",   domain, priority,
                     context=f"{by_status.get('Activo',0)} activos de {total}"))
    kpis.append(_kpi("recurring_pct",  "Recurrencia (≥4m)",   rec_pct,    "pct",   domain, priority,
                     context="Compraron ≥4 meses en últimos 12m"))
    kpis.append(_kpi("avg_ticket",     "Ticket promedio",     avg_t,      "money", domain, priority))
    kpis.append(_kpi("churn_at_risk",  "Venta en riesgo",     at_risk,    "money", domain, priority,
                     context=f"{dormant+react} clientes dormidos/reactivables"))

    rat["active_pct"]    = active_pct
    rat["recurring_pct"] = rec_pct
    rat["churn_pct"]     = churn_pct
    rat["at_risk_sales"] = round(at_risk, 2)

    sem.append(_semaphore("cartera_activa",   "Cartera activa",  active_pct,
                          _thresholds("active_pct"),    f"Activos: {active_pct}%"))
    sem.append(_semaphore("recurrencia",      "Recurrencia",     rec_pct,
                          _thresholds("recurring_pct"), f"Recurrentes: {rec_pct}%"))
    return kpis, sem, rat


def _extract_cross_sell(result, domain, priority):
    kpis, sem, rat = [], [], {}

    avg_b = result.get("avg_breadth", 0.0)
    low_b = result.get("low_breadth_clients", 0)
    total = len(result.get("by_client", [])) or 1
    dist  = result.get("breadth_distribution", {})
    max_b = max(dist.keys(), default=1)

    low_pct = round(low_b / total * 100, 1)

    kpis.append(_kpi("avg_breadth",       "Familias promedio / cliente", avg_b, "text",  domain, priority,
                     context=f"Máx detectable: {max_b}"))
    kpis.append(_kpi("low_breadth_pct",   "Clientes de baja profundidad", low_pct, "pct", domain, priority,
                     context=f"{low_b} clientes con ≤1 categoría"))

    rat["avg_breadth"]   = avg_b
    rat["low_breadth_pct"] = low_pct

    sem.append(_semaphore("profundidad", "Amplitud de surtido", avg_b,
                          _thresholds("avg_breadth"), f"Promedio {avg_b:.1f} familias"))
    return kpis, sem, rat


def _extract_seller(result, domain, priority):
    kpis, sem, rat = [], [], {}

    top3  = result.get("concentration_top3_pct", 0.0)
    total = result.get("total", 0.0)
    n     = len(result.get("sellers", []))
    fld   = result.get("seller_field", "vendedor")

    kpis.append(_kpi("seller_total",        f"Total ({fld})",      total, "money", domain, priority,
                     context=f"{n} vendedores"))
    kpis.append(_kpi("seller_top3_share",   "Concentración top 3", top3,  "pct",   domain, priority,
                     context="Participación de los 3 principales"))
    kpis.append(_kpi("seller_count",        "Vendedores activos",  n,     "int",   domain, priority))

    rat["seller_concentration_pct"] = top3

    # Rojo si top3 > 70% (dependencia alta)
    color = "green" if top3 < 50 else ("yellow" if top3 < 70 else "red")
    sem.append({"id": "seller_conc", "label": "Dependencia comercial", "color": color,
                "value": top3, "detail": f"Top 3 vendedores: {top3}%"})
    return kpis, sem, rat


def _extract_geographic(result, domain, priority):
    kpis, sem, rat = [], [], {}

    uncov_pct = result.get("uncovered_pct", 0.0)
    total     = result.get("total", 0.0)
    n         = len(result.get("routes", []))
    cov_pct   = round(100 - uncov_pct, 1)

    kpis.append(_kpi("geo_total",   "Total con ruta",  total,    "money", domain, priority,
                     context=f"{n} rutas"))
    kpis.append(_kpi("geo_cov_pct", "Cobertura rutas", cov_pct,  "pct",   domain, priority,
                     context=f"Sin ruta: {_fmt_money(result.get('uncovered_value',0))}"))

    rat["route_coverage_pct"] = cov_pct

    sem.append(_semaphore("cobertura_ruta", "Cobertura rutas", cov_pct,
                          _thresholds("coverage_pct"), f"Ventas con ruta: {cov_pct}%"))
    return kpis, sem, rat


def _extract_margin(result, domain, priority):
    kpis, sem, rat = [], [], {}

    t_rev = result.get("total_revenue", 0.0)
    t_mgn = result.get("total_margin", 0.0)
    m_pct = result.get("total_margin_pct", 0.0)

    kpis.append(_kpi("total_revenue", "Facturación total",  t_rev,  "money", domain, priority))
    kpis.append(_kpi("total_margin",  "Margen bruto total", t_mgn,  "money", domain, priority))
    kpis.append(_kpi("margin_pct",    "Margen bruto %",     m_pct,  "pct",   domain, priority))

    rat["margin_pct"] = m_pct

    sem.append(_semaphore("margen", "Margen bruto", m_pct,
                          _thresholds("margin_pct"), f"Margen: {m_pct}%"))
    return kpis, sem, rat


def _extract_sales_force(result, domain, priority):
    kpis, sem, rat = [], [], {}

    top3  = result.get("concentration_top3_pct", 0.0)
    total = result.get("total", 0.0)
    n     = len(result.get("forces", result.get("sellers", [])))

    kpis.append(_kpi("force_total",    "Total fuerza ventas",  total, "money", domain, priority,
                     context=f"{n} fuerzas"))
    kpis.append(_kpi("force_top3_share", "Concentración top 3", top3,  "pct",   domain, priority))

    rat["force_concentration_pct"] = top3
    return kpis, sem, rat


def _extract_product(result, domain, priority):
    kpis, sem, rat = [], [], {}

    items    = result.get("items", [])
    total    = result.get("total", 0.0)
    top_cat  = result.get("top_category", "-")
    n        = len(items)

    top3_val = sum(i["value"] for i in items[:3])
    top3_pct = round(top3_val / max(total, 1) * 100, 1)

    growing   = sum(1 for i in items if i.get("growth_pct", 0) > 5)
    declining = sum(1 for i in items if i.get("growth_pct", 0) < -10)

    kpis.append(_kpi("product_total",    "Venta total productos", total,    "money", domain, priority,
                     context=f"{n} categorías"))
    kpis.append(_kpi("product_top3_pct", "Top 3 familias",        top3_pct, "pct",   domain, priority,
                     context=f"Liderado por {top_cat}"))
    kpis.append(_kpi("growing_cats",     "Familias en crecimiento", growing, "int",   domain, priority,
                     context=f"{declining} en caída (>10%)"))

    rat["product_concentration_pct"] = top3_pct
    rat["growing_categories"]        = growing
    rat["declining_categories"]      = declining
    return kpis, sem, rat


def _extract_channel(result, domain, priority):
    kpis, sem, rat = [], [], {}

    items   = result.get("items", [])
    total   = result.get("total", 0.0)
    n       = result.get("channel_count", len(items))
    top_ch  = result.get("top_channel", "-")
    top_pct = items[0]["share_pct"] if items else 0.0

    kpis.append(_kpi("channel_total",   "Venta total canales", total,   "money", domain, priority,
                     context=f"{n} canales"))
    kpis.append(_kpi("top_channel_pct", f"Canal líder ({top_ch})", top_pct, "pct", domain, priority,
                     context="Participación del canal principal"))

    rat["top_channel_pct"] = top_pct
    return kpis, sem, rat


# Registro de extractores → un extractor por task_id
_EXTRACTORS = {
    "temporal_trend":        _extract_temporal,
    "dimension_ranking":     _extract_ranking,
    "recurrence_churn":      _extract_churn,
    "cross_sell_mix":        _extract_cross_sell,
    "seller_performance":    _extract_seller,
    "geographic_coverage":   _extract_geographic,
    "margin_analysis":       _extract_margin,
    "sales_force_breakdown": _extract_sales_force,
    "product_analysis":      _extract_product,
    "channel_analysis":      _extract_channel,
}


# ---------------------------------------------------------------------------
# Constructores internos
# ---------------------------------------------------------------------------

def _kpi(kid, label, value, fmt, domain, priority, delta=None, context=""):
    delta_dir = "flat"
    if delta is not None:
        delta_dir = "up" if delta > 0.5 else ("down" if delta < -0.5 else "flat")
    return {
        "id":        kid,
        "label":     label,
        "value":     round(value, 2) if isinstance(value, float) else value,
        "format":    fmt,
        "delta":     round(delta, 1) if delta is not None else None,
        "delta_dir": delta_dir,
        "context":   context,
        "domain":    domain,
        "priority":  priority,
    }


def _semaphore(sid, label, value, thresholds, detail):
    lo, hi = thresholds
    # Determinar si el umbral es "invertido" (mayor = peor)
    inverted = lo > hi
    if inverted:
        color = "green" if value < hi else ("yellow" if value < lo else "red")
    else:
        color = "green" if value >= hi else ("yellow" if value >= lo else "red")
    return {"id": sid, "label": label, "color": color, "value": round(value, 1), "detail": detail}


def _thresholds(key):
    return _THRESHOLDS.get(key, (0.0, 100.0))


# ---------------------------------------------------------------------------
# Utilidades de formato (solo para strings internos, no para la UI)
# ---------------------------------------------------------------------------

def _fmt_money(value):
    try:
        return f"${value:,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def _sign(value):
    return "+" if value >= 0 else ""


def _hhi_label(hhi):
    if hhi < 1000:
        return "mercado fragmentado"
    if hhi < 2500:
        return "concentración moderada"
    return "alta concentración"
