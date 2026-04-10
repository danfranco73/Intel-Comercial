"""
insight_writer.py — Fase 5 del motor dinámico de análisis comercial.

Responsabilidad:
    Convertir el KpiSet y los resultados del AnalysisEngine en frases de
    análisis ejecutivo en español, listas para mostrar en la UI sin
    post-procesamiento adicional.

    Trabaja exclusivamente sobre estructuras normalizadas; no conoce nombres
    de columna del Excel.

Estructura de salida — Insight:
    {
        "id":       str,
        "domain":   str,            ← "tiempo"|"cartera"|"territorio"|
                                       "producto"|"canal"|"margen"|"fuerza"
        "priority": int,            ← 1=alta, 2=media, 3=baja
        "type":     str,            ← "trend"|"alert"|"opportunity"|
                                       "positive"|"context"
        "text":     str,            ← frase ejecutiva en español
        "tags":     [str],
    }

API:
    from insight_writer import write_insights
    insights = write_insights(kpi_set, engine_results, tasks)
    # → list[Insight] ordenados por (priority, domain)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Formatters de apoyo
# ---------------------------------------------------------------------------

def _money(v: float) -> str:
    """Formatea número como moneda abreviada."""
    v = abs(v)
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _pct(v: float, decimals: int = 1) -> str:
    return f"{v:.{decimals}f}%"


def _sign(v: float) -> str:
    return "+" if v >= 0 else ""


def _trend_word(pct: float) -> str:
    if pct >= 10:
        return "creció significativamente"
    if pct >= 3:
        return "creció"
    if pct >= 0:
        return "se mantuvo estable"
    if pct >= -10:
        return "cayó"
    return "cayó significativamente"


def _coverage_adj(pct: float) -> str:
    if pct >= 95:
        return "excelente"
    if pct >= 85:
        return "aceptable"
    if pct >= 70:
        return "parcial"
    return "insuficiente"


def _margin_adj(pct: float) -> str:
    if pct >= 25:
        return "saludable"
    if pct >= 15:
        return "aceptable"
    if pct >= 8:
        return "ajustado"
    return "crítico"


def _breadth_adj(avg: float) -> str:
    if avg >= 4:
        return "alta"
    if avg >= 2.5:
        return "media"
    return "baja"


# ---------------------------------------------------------------------------
# Constructores internos
# ---------------------------------------------------------------------------

def _insight(iid, domain, priority, itype, text, tags=None):
    return {
        "id":       iid,
        "domain":   domain,
        "priority": priority,
        "type":     itype,
        "text":     text,
        "tags":     tags or [],
    }


# ---------------------------------------------------------------------------
# Generadores por dominio
# ---------------------------------------------------------------------------

def _gen_temporal(result: dict, ratios: dict) -> list:
    ins = []
    trend   = result.get("trend_pct", 0.0)
    base    = result.get("last3_avg", 0.0)
    periods = result.get("periods", 0)
    forecast = result.get("forecast", [])

    if base == 0 and periods == 0:
        return ins

    # Tendencia principal
    itype = "positive" if trend >= 3 else ("alert" if trend < 0 else "trend")
    ins.append(_insight(
        "tempo_trend", "tiempo", 1, itype,
        f"La venta {_trend_word(trend)} ({_sign(trend)}{_pct(trend)}) "
        f"respecto al bloque previo equivalente, con una base media de {_money(base)} "
        f"sobre {periods} períodos analizados.",
        ["tendencia", "crecimiento"],
    ))

    # Proyección si existe
    if forecast:
        proj_q = sum(p["value"] for p in forecast[:3])
        ins.append(_insight(
            "tempo_forecast", "tiempo", 2, "context",
            f"Con la tendencia actual, la proyección para la próxima ventana "
            f"es de {_money(proj_q)}.",
            ["proyeccion", "forecast"],
        ))

    # Estacionalidad si hay suficientes períodos
    if periods >= 12:
        ins.append(_insight(
            "tempo_seasonal", "tiempo", 3, "context",
            f"El análisis cubre {periods} períodos: hay base suficiente para "
            f"detectar estacionalidad y patrones de cierre.",
            ["estacionalidad"],
        ))

    return ins


def _gen_ranking(result: dict, ratios: dict) -> list:
    ins = []
    top3  = result.get("concentration_top3_pct", 0.0)
    hhi   = result.get("hhi", 0.0)
    items = result.get("items", [])
    dim   = result.get("dimension_field", "dimensión")

    if not items:
        return ins

    top_label = items[0]["label"] if items else "—"
    top_share = items[0].get("share_pct", 0.0)

    # Concentración alta → alerta
    if top3 >= 60:
        ins.append(_insight(
            "rank_concentration", "cartera", 1, "alert",
            f"El top 3 de {dim} concentra el {_pct(top3)} de la venta total "
            f"(HHI {hhi:.0f}), lo que representa un riesgo de dependencia.",
            ["concentracion", "riesgo"],
        ))
    elif top3 >= 40:
        ins.append(_insight(
            "rank_concentration", "cartera", 2, "trend",
            f"La concentración en el top 3 de {dim} es del {_pct(top3)} "
            f"(HHI {hhi:.0f}) — distribución moderada.",
            ["concentracion"],
        ))
    else:
        ins.append(_insight(
            "rank_concentration", "cartera", 2, "positive",
            f"La cartera está bien distribuida: el top 3 de {dim} representa "
            f"solo el {_pct(top3)} (HHI {hhi:.0f}).",
            ["concentracion", "diversificacion"],
        ))

    # Líder de ranking
    if top_share >= 15:
        ins.append(_insight(
            "rank_leader", "cartera", 2, "context",
            f"'{top_label}' lidera con el {_pct(top_share)} de participación.",
            ["lider", "ranking"],
        ))

    return ins


def _gen_churn(result: dict, ratios: dict) -> list:
    ins = []
    total      = result.get("total_clients", 1)
    active_pct = result.get("active_pct", 0.0)
    rec_pct    = result.get("recurring_pct", 0.0)
    by_status  = result.get("by_status", {})
    at_risk    = result.get("churn_at_risk_sales", 0.0)
    dormant    = by_status.get("Dormido", 0)
    react      = by_status.get("Reactivable", 0)
    lost       = by_status.get("Perdido", 0)

    if total == 0:
        return ins

    # Cartera activa
    if active_pct < 55:
        ins.append(_insight(
            "churn_active_low", "cartera", 1, "alert",
            f"Solo el {_pct(active_pct)} de la cartera está activa ({by_status.get('Activo',0)} "
            f"de {total} clientes). Hay un problema serio de retención.",
            ["churn", "cartera_activa"],
        ))
    elif active_pct >= 75:
        ins.append(_insight(
            "churn_active_ok", "cartera", 2, "positive",
            f"El {_pct(active_pct)} de la cartera está activa — nivel saludable "
            f"sobre un universo de {total} clientes.",
            ["cartera_activa"],
        ))
    else:
        ins.append(_insight(
            "churn_active_mid", "cartera", 2, "trend",
            f"La cartera activa llega al {_pct(active_pct)} ({by_status.get('Activo',0)} clientes). "
            f"Hay margen de mejora en retención.",
            ["cartera_activa"],
        ))

    # Recurrencia
    if rec_pct < 40:
        ins.append(_insight(
            "churn_rec_low", "cartera", 1, "alert",
            f"La recurrencia real es de solo {_pct(rec_pct)}: menos de la mitad "
            f"de los clientes activos compra con regularidad.",
            ["recurrencia", "frecuencia"],
        ))
    elif rec_pct >= 60:
        ins.append(_insight(
            "churn_rec_ok", "cartera", 2, "positive",
            f"Buena recurrencia: el {_pct(rec_pct)} de clientes compró "
            f"4 o más meses en el último año.",
            ["recurrencia"],
        ))

    # Oportunidad de recuperación
    recoverable = dormant + react
    if recoverable > 0 and at_risk > 0:
        ins.append(_insight(
            "churn_opportunity", "cartera", 1, "opportunity",
            f"Hay {recoverable} clientes dormidos o reactivables "
            f"({dormant} dormidos, {react} reactivables) con {_money(at_risk)} "
            f"en venta en riesgo. Son la palanca de recuperación más inmediata.",
            ["recuperacion", "oportunidad"],
        ))

    # Clientes perdidos
    if lost > 0:
        ins.append(_insight(
            "churn_lost", "cartera", 2, "alert",
            f"{lost} clientes están clasificados como perdidos "
            f"(sin compra en más de 4 meses). Requieren acción de reconquista.",
            ["churn", "perdidos"],
        ))

    return ins


def _gen_cross_sell(result: dict, ratios: dict) -> list:
    ins = []
    avg_b  = result.get("avg_breadth", 0.0)
    low_b  = result.get("low_breadth_clients", 0)
    total  = result.get("total_clients_mix", low_b + 1)

    if avg_b == 0:
        return ins

    ins.append(_insight(
        "mix_breadth", "producto", 2, "trend",
        f"Los clientes activos compran en promedio {avg_b:.1f} "
        f"{'categorías' if avg_b != 1 else 'categoría'} — amplitud {_breadth_adj(avg_b)}.",
        ["surtido", "profundidad"],
    ))

    if low_b > 0:
        low_pct = round(low_b / max(total, 1) * 100, 1)
        ins.append(_insight(
            "mix_opportunity", "producto", 1, "opportunity",
            f"{low_b} clientes ({_pct(low_pct)}) compran solo 1 categoría: "
            f"son candidatos directos a campañas de cross-selling.",
            ["cross_sell", "oportunidad"],
        ))

    return ins


def _gen_seller(result: dict, ratios: dict) -> list:
    ins = []
    sellers = result.get("sellers", [])
    top3    = result.get("concentration_top3_pct", 0.0)

    if not sellers:
        return ins

    top     = sellers[0]
    top_g   = top.get("growth_pct", 0.0)
    neg_cnt = sum(1 for s in sellers if s.get("growth_pct", 0) < 0)

    if top3 >= 70:
        ins.append(_insight(
            "seller_concentration", "territorio", 1, "alert",
            f"El equipo de ventas presenta alta dependencia: los 3 primeros "
            f"vendedores representan el {_pct(top3)} de la facturación.",
            ["concentracion", "vendedores"],
        ))

    if top_g >= 10:
        ins.append(_insight(
            "seller_leader", "territorio", 2, "positive",
            f"'{top['seller']}' lidera el equipo con un crecimiento de "
            f"{_sign(top_g)}{_pct(top_g)} en el período.",
            ["vendedor_lider"],
        ))
    elif top_g < 0:
        ins.append(_insight(
            "seller_leader_down", "territorio", 1, "alert",
            f"El vendedor líder '{top['seller']}' registra caída de {_pct(abs(top_g))} — "
            f"su desempeño impacta directamente al volumen total.",
            ["vendedor_lider", "alerta"],
        ))

    if neg_cnt > 0:
        total_s = len(sellers)
        ins.append(_insight(
            "seller_negatives", "territorio", 2, "alert" if neg_cnt > total_s // 2 else "trend",
            f"{neg_cnt} de {total_s} vendedores muestra caída interanual. "
            f"{'La mayoría del equipo está en retroceso.' if neg_cnt > total_s // 2 else 'Requiere seguimiento individual.'}",
            ["vendedores", "desempeno"],
        ))

    return ins


def _gen_geographic(result: dict, ratios: dict) -> list:
    ins = []
    uncov_pct = result.get("uncovered_pct", 0.0)
    uncov_val = result.get("uncovered_value", 0.0)
    routes    = result.get("routes", [])
    n_routes  = len(routes)
    covered   = sum(1 for r in routes if r.get("covered", False))

    if n_routes == 0:
        return ins

    cov_pct = round(covered / n_routes * 100, 1)

    if uncov_pct >= 20:
        ins.append(_insight(
            "geo_coverage_low", "territorio", 1, "alert",
            f"El {_pct(uncov_pct)} de rutas no tiene asignación completa de vendedor "
            f"— hay {_money(uncov_val)} de venta sin cobertura definida.",
            ["cobertura", "rutas"],
        ))
    elif uncov_pct >= 5:
        ins.append(_insight(
            "geo_coverage_mid", "territorio", 2, "trend",
            f"La cobertura geográfica es {_coverage_adj(cov_pct)} ({_pct(cov_pct)} de rutas cubiertas). "
            f"Quedan {_money(uncov_val)} sin asignar.",
            ["cobertura"],
        ))
    else:
        ins.append(_insight(
            "geo_coverage_ok", "territorio", 3, "positive",
            f"Cobertura geográfica {_coverage_adj(cov_pct)} en {n_routes} rutas — "
            f"el territorio está bien gestionado.",
            ["cobertura"],
        ))

    return ins


def _gen_margin(result: dict, ratios: dict) -> list:
    ins = []
    margin_pct = result.get("total_margin_pct", 0.0)
    items      = result.get("items", [])
    low_items  = [i for i in items if i.get("margin_pct", 99) < 10]

    if margin_pct == 0:
        return ins

    adj = _margin_adj(margin_pct)
    itype = "positive" if margin_pct >= 20 else ("alert" if margin_pct < 10 else "trend")
    ins.append(_insight(
        "margin_total", "margen", 1, itype,
        f"El margen bruto promedio ponderado es {_pct(margin_pct)} — nivel {adj}.",
        ["margen", "rentabilidad"],
    ))

    if low_items:
        names = ", ".join(i["label"] for i in low_items[:3])
        ins.append(_insight(
            "margin_low_items", "margen", 1, "alert",
            f"{len(low_items)} segmento(s) operan con margen menor al 10%: {names}. "
            f"Requieren revisión de precios o costos.",
            ["margen_bajo", "rentabilidad"],
        ))

    if items:
        top_margin = max(items, key=lambda i: i.get("margin_pct", 0))
        if top_margin.get("margin_pct", 0) >= 25:
            ins.append(_insight(
                "margin_leader", "margen", 2, "opportunity",
                f"'{top_margin['label']}' tiene el mayor margen ({_pct(top_margin['margin_pct'])}): "
                f"escalar este segmento mejoraría la rentabilidad global.",
                ["margen_alto", "oportunidad"],
            ))

    return ins


def _gen_sales_force(result: dict, ratios: dict) -> list:
    ins = []
    forces = result.get("forces", result.get("sellers", []))

    if not forces:
        return ins

    total_v = sum(f["value"] for f in forces)
    top_f   = forces[0] if forces else None

    if top_f and total_v > 0:
        top_share = round(top_f["value"] / total_v * 100, 1)
        ins.append(_insight(
            "force_leader", "fuerza", 2, "context",
            f"La fuerza de ventas '{top_f.get('seller', top_f.get('force', '?'))}' "
            f"lidera con el {_pct(top_share)} del volumen total.",
            ["fuerza_ventas"],
        ))

    if len(forces) > 1:
        # Chequeamos si hay fuerzas muy desequilibradas
        values = [f["value"] for f in forces]
        max_v, min_v = max(values), min(values)
        if min_v > 0 and max_v / min_v >= 5:
            ins.append(_insight(
                "force_imbalance", "fuerza", 2, "alert",
                f"Hay un desequilibrio marcado entre fuerzas de ventas "
                f"(ratio {max_v/min_v:.1f}x entre la mayor y la menor). "
                f"Revisar distribución territorial o de cartera.",
                ["fuerza_ventas", "desequilibrio"],
            ))

    return ins


def _gen_product(result: dict, ratios: dict) -> list:
    ins = []
    items    = result.get("items", [])
    top_cat  = result.get("top_category")

    if not items:
        return ins

    declining = [i for i in items if i.get("growth_pct", 0) < -5]
    growing   = [i for i in items if i.get("growth_pct", 0) >= 10]

    if top_cat:
        ins.append(_insight(
            "prod_leader", "producto", 2, "context",
            f"La categoría '{top_cat}' lidera las ventas de producto.",
            ["producto_lider"],
        ))

    if growing:
        cats = ", ".join(i["label"] for i in growing[:3])
        ins.append(_insight(
            "prod_growing", "producto", 2, "positive",
            f"{len(growing)} {'categoría crece' if len(growing)==1 else 'categorías crecen'} "
            f"más del 10%: {cats}.",
            ["crecimiento", "producto"],
        ))

    if declining:
        cats = ", ".join(i["label"] for i in declining[:3])
        ins.append(_insight(
            "prod_declining", "producto", 1, "alert",
            f"{len(declining)} {'categoría cae' if len(declining)==1 else 'categorías caen'} "
            f"más del 5%: {cats}. Revisar foco comercial.",
            ["declinacion", "producto"],
        ))

    return ins


def _gen_channel(result: dict, ratios: dict) -> list:
    ins = []
    items    = result.get("items", [])
    top_ch   = result.get("top_channel")

    if not items or not top_ch:
        return ins

    top_item  = next((i for i in items if i["label"] == top_ch), None)
    top_share = top_item["share_pct"] if top_item else 0.0

    if top_share >= 80:
        ins.append(_insight(
            "channel_mono", "canal", 2, "alert",
            f"El canal '{top_ch}' concentra el {_pct(top_share)} del negocio. "
            f"Alta dependencia de un único canal de distribución.",
            ["canal", "concentracion"],
        ))
    elif top_share >= 50:
        ins.append(_insight(
            "channel_dominant", "canal", 2, "trend",
            f"El canal '{top_ch}' lidera con {_pct(top_share)} de participación. "
            f"Hay oportunidad de desarrollar canales secundarios.",
            ["canal", "diversificacion"],
        ))
    else:
        ins.append(_insight(
            "channel_balanced", "canal", 3, "positive",
            f"La distribución por canal es equilibrada: '{top_ch}' lidera con solo "
            f"{_pct(top_share)}.",
            ["canal", "diversificacion"],
        ))

    return ins


# ---------------------------------------------------------------------------
# Registro de generadores
# ---------------------------------------------------------------------------

_GENERATORS = {
    "temporal_trend":        _gen_temporal,
    "dimension_ranking":     _gen_ranking,
    "recurrence_churn":      _gen_churn,
    "cross_sell_mix":        _gen_cross_sell,
    "seller_performance":    _gen_seller,
    "geographic_coverage":   _gen_geographic,
    "margin_analysis":       _gen_margin,
    "sales_force_breakdown": _gen_sales_force,
    "product_analysis":      _gen_product,
    "channel_analysis":      _gen_channel,
}

_DOMAIN_ORDER = [
    "tiempo", "cartera", "territorio", "producto", "canal", "margen", "fuerza", "general"
]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def write_insights(kpi_set: dict, engine_results: dict, tasks: list) -> list:
    """
    Genera la lista de Insights ejecutivos a partir del KpiSet y los
    resultados del AnalysisEngine.

    Args:
        kpi_set:        KpiSet (salida de kpi_generator.generate_kpis)
        engine_results: dict[task_id → result_dict]
        tasks:          list[AnalysisTask]

    Returns:
        list[Insight] — ordenados por (priority, domain_order)
    """
    ratios   = kpi_set.get("ratios", {})
    all_ins  = []

    for task in tasks:
        task_id = task["id"]
        result  = engine_results.get(task_id, {})
        if "error" in result:
            continue
        gen = _GENERATORS.get(task_id)
        if gen is None:
            continue
        all_ins.extend(gen(result, ratios))

    # Desduplicar por id (puede haber colisiones si se llama dos veces)
    seen = set()
    unique = []
    for ins in all_ins:
        if ins["id"] not in seen:
            seen.add(ins["id"])
            unique.append(ins)

    # Ordenar: primero por prioridad, luego por dominio
    unique.sort(key=lambda i: (
        i["priority"],
        _DOMAIN_ORDER.index(i["domain"]) if i["domain"] in _DOMAIN_ORDER else 99,
    ))

    return unique


def insights_by_type(insights: list) -> dict:
    """Agrupa la lista de Insights por tipo."""
    groups = {}
    for ins in insights:
        groups.setdefault(ins["type"], []).append(ins)
    return groups


def insights_summary(insights: list) -> dict:
    """Resumen contable para la UI."""
    by_type = insights_by_type(insights)
    return {
        "total":       len(insights),
        "alerts":      len(by_type.get("alert", [])),
        "opportunities": len(by_type.get("opportunity", [])),
        "positives":   len(by_type.get("positive", [])),
        "trends":      len(by_type.get("trend", [])),
        "contexts":    len(by_type.get("context", [])),
    }
