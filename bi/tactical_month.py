from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from statistics import median


MONTH_NAMES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}


def _client_id(record):
    return record.get("client_canonical") or record.get("client_key") or record.get("client")


def _records_between(records, start_date, end_date):
    return [item for item in records if start_date <= item["date"] <= end_date]


def _sum_amount(records):
    return round(sum(item.get("amount", 0) or 0 for item in records), 2)


def _sum_quantity(records):
    return round(sum(item.get("quantity", 0) or 0 for item in records), 2)


def _unique_clients(records):
    return {_client_id(item) for item in records if _client_id(item)}


def _unique_orders(records):
    return {
        item.get("invoice")
        for item in records
        if item.get("invoice")
    }


def _safe_pct(part, total):
    return round((part / max(total, 1)) * 100, 1)


def _pct_change(current, previous):
    if abs(previous) < 0.0001:
        if abs(current) < 0.0001:
            return 0.0
        return 100.0
    return round(((current - previous) / abs(previous)) * 100, 1)


def _first_day(value):
    return value.replace(day=1)


def _last_day(value):
    return value.replace(day=monthrange(value.year, value.month)[1])


def _shift_month(value, months):
    month_index = (value.year * 12 + value.month - 1) + months
    year_value = month_index // 12
    month_value = month_index % 12 + 1
    day_value = min(value.day, monthrange(year_value, month_value)[1])
    return date(year_value, month_value, day_value)


def _same_day_previous_year(value):
    year_value = value.year - 1
    day_value = min(value.day, monthrange(year_value, value.month)[1])
    return date(year_value, value.month, day_value)


def _month_label(value):
    return f"{MONTH_NAMES.get(value.month, value.month)} {value.year}"


def _day_labels(start_date, end_date):
    labels = []
    cursor = start_date
    while cursor <= end_date:
        labels.append(cursor.strftime("%d/%m"))
        cursor += timedelta(days=1)
    return labels


def _cumulative_daily(records, start_date, end_date, metric_key):
    daily = defaultdict(float)
    for item in records:
        if start_date <= item["date"] <= end_date:
            daily[item["date"]] += item.get(metric_key, 0) or 0
    values = []
    running = 0.0
    cursor = start_date
    while cursor <= end_date:
        running += daily.get(cursor, 0.0)
        values.append(round(running, 2))
        cursor += timedelta(days=1)
    return values


def _aggregate_dimension(records, field, metric_key="amount", missing_label=None):
    grouped = defaultdict(lambda: {"sales": 0.0, "quantity": 0.0, "clients": set(), "orders": set()})
    for item in records:
        label = item.get(field) or missing_label or "Sin dato"
        bucket = grouped[label]
        bucket["sales"] += item.get("amount", 0) or 0
        bucket["quantity"] += item.get("quantity", 0) or 0
        if _client_id(item):
            bucket["clients"].add(_client_id(item))
        if item.get("invoice"):
            bucket["orders"].add(item.get("invoice"))
    stats = []
    for label, values in grouped.items():
        sales = round(values["sales"], 2)
        quantity = round(values["quantity"], 2)
        clients = len(values["clients"])
        orders = len(values["orders"])
        stats.append(
            {
                "label": label,
                "sales": sales,
                "quantity": quantity,
                "clients": clients,
                "orders": orders,
                "avgSalesPerClient": round(sales / max(clients, 1), 2),
                "avgUnitsPerClient": round(quantity / max(clients, 1), 2),
                "avgUnitsPerOrder": round(quantity / max(orders, 1), 2),
            }
        )
    return stats


def _aggregate_clients(records):
    grouped = defaultdict(list)
    for item in records:
        key = _client_id(item)
        if key:
            grouped[key].append(item)
    rows = []
    for client_id, items in grouped.items():
        items.sort(key=lambda item: item["date"])
        sales = _sum_amount(items)
        quantity = _sum_quantity(items)
        orders = len(_unique_orders(items))
        families = len({item.get("family") for item in items if item.get("family") and not str(item.get("family")).lower().startswith("sin ")})
        rows.append(
            {
                "client_id": client_id,
                "client": items[-1].get("client") or items[-1].get("client_name") or items[-1].get("client_key"),
                "sales": sales,
                "quantity": quantity,
                "orders": orders,
                "avgTicket": round(sales / max(orders, 1), 2),
                "avgUnitsPerOrder": round(quantity / max(orders, 1), 2),
                "families": families,
                "lastDate": items[-1]["date"].isoformat(),
                "seller": _most_common(items, "seller_name", "Sin vendedor"),
                "route": _most_common(items, "route_description", "Sin ruta"),
                "salesForce": _most_common(items, "sales_force", "Sin fuerza de ventas"),
            }
        )
    return rows


def _most_common(records, field, fallback):
    counter = defaultdict(int)
    for item in records:
        value = item.get(field) or fallback
        counter[value] += 1
    if not counter:
        return fallback
    return sorted(counter.items(), key=lambda pair: pair[1], reverse=True)[0][0]


def _dimension_with_previous(current_records, previous_records, field, missing_label):
    current_map = {item["label"]: item for item in _aggregate_dimension(current_records, field, missing_label=missing_label)}
    previous_map = {item["label"]: item for item in _aggregate_dimension(previous_records, field, missing_label=missing_label)}
    labels = set(current_map) | set(previous_map)
    rows = []
    for label in labels:
        current = current_map.get(label, {})
        previous = previous_map.get(label, {})
        current_sales = current.get("sales", 0.0)
        previous_sales = previous.get("sales", 0.0)
        current_clients = current.get("clients", 0)
        previous_clients = previous.get("clients", 0)
        rows.append(
            {
                "label": label,
                "sales": round(current_sales, 2),
                "previousSales": round(previous_sales, 2),
                "quantity": round(current.get("quantity", 0.0), 2),
                "clients": current_clients,
                "previousClients": previous_clients,
                "orders": current.get("orders", 0),
                "salesGrowthPct": _pct_change(current_sales, previous_sales),
                "clientDelta": current_clients - previous_clients,
                "sharePct": 0.0,
            }
        )
    total_sales = sum(item["sales"] for item in rows)
    for row in rows:
        row["sharePct"] = _safe_pct(row["sales"], total_sales)
    return rows


def _month_range(selected_end):
    current_start = selected_end.replace(day=1)
    current_end = selected_end
    previous_same_day = _shift_month(selected_end, -1)
    previous_start = previous_same_day.replace(day=1)
    previous_end = previous_same_day
    previous_full_end = _last_day(previous_start)
    yoy_end = _same_day_previous_year(selected_end)
    yoy_start = yoy_end.replace(day=1)
    yoy_full_end = _last_day(yoy_start)
    return {
        "currentStart": current_start,
        "currentEnd": current_end,
        "previousStart": previous_start,
        "previousEnd": previous_end,
        "previousFullEnd": previous_full_end,
        "yoyStart": yoy_start,
        "yoyEnd": yoy_end,
        "yoyFullEnd": yoy_full_end,
        "daysElapsed": (current_end - current_start).days + 1,
        "daysInMonth": monthrange(current_end.year, current_end.month)[1],
    }


def _build_objective(current_end, records):
    current_start = current_end.replace(day=1)
    previous_month_start = _shift_month(current_start, -1)
    previous_month_end = _last_day(previous_month_start)
    same_month_last_year = date(current_end.year - 1, current_end.month, 1)
    same_month_last_year_end = _last_day(same_month_last_year)

    benchmark_sales = []
    benchmark_units = []
    previous_full_records = _records_between(records, previous_month_start, previous_month_end)
    if previous_full_records:
        benchmark_sales.append(_sum_amount(previous_full_records))
        benchmark_units.append(_sum_quantity(previous_full_records))

    yoy_full_records = _records_between(records, same_month_last_year, same_month_last_year_end)
    if yoy_full_records:
        benchmark_sales.append(_sum_amount(yoy_full_records))
        benchmark_units.append(_sum_quantity(yoy_full_records))

    trailing_sales = []
    trailing_units = []
    for offset in range(1, 4):
        month_start = _shift_month(current_start, -offset)
        month_end = _last_day(month_start)
        month_records = _records_between(records, month_start, month_end)
        if month_records:
            trailing_sales.append(_sum_amount(month_records))
            trailing_units.append(_sum_quantity(month_records))
    if trailing_sales:
        benchmark_sales.append(round(sum(trailing_sales) / len(trailing_sales), 2))
        benchmark_units.append(round(sum(trailing_units) / len(trailing_units), 2))

    drivers = []
    if previous_full_records:
        drivers.append({
            "label": "Mes anterior completo",
            "sales": _sum_amount(previous_full_records),
            "units": _sum_quantity(previous_full_records),
        })
    if yoy_full_records:
        drivers.append({
            "label": "Mismo mes año anterior",
            "sales": _sum_amount(yoy_full_records),
            "units": _sum_quantity(yoy_full_records),
        })
    if trailing_sales:
        drivers.append({
            "label": "Promedio móvil 3M",
            "sales": round(sum(trailing_sales) / len(trailing_sales), 2),
            "units": round(sum(trailing_units) / len(trailing_units), 2),
        })

    objective_sales_close = round(median(benchmark_sales), 2) if benchmark_sales else 0.0
    objective_units_close = round(median(benchmark_units), 2) if benchmark_units else 0.0
    return {
        "salesClose": objective_sales_close,
        "unitsClose": objective_units_close,
        "drivers": drivers,
        "method": "Mediana de benchmarks",
    }


def _scenario_tone(gap_pct):
    if gap_pct >= 2:
        return "good"
    if gap_pct >= -3:
        return "warn"
    return "bad"


def _build_sensitivity(current_sales, current_units, days_elapsed, days_in_month, objective_sales_close, objective_units_close):
    remaining_days = max(days_in_month - days_elapsed, 0)
    current_sales_per_day = round(current_sales / max(days_elapsed, 1), 2)
    current_units_per_day = round(current_units / max(days_elapsed, 1), 2)
    required_sales_per_day = round(max(objective_sales_close - current_sales, 0) / max(remaining_days, 1), 2)
    required_units_per_day = round(max(objective_units_close - current_units, 0) / max(remaining_days, 1), 2)
    scenarios = []
    for label, pace_delta_pct in [
        ("Conservador", -12),
        ("Ajuste leve", -5),
        ("Ritmo actual", 0),
        ("Recuperación", 8),
        ("Aceleración", 15),
    ]:
        multiplier = 1 + pace_delta_pct / 100
        projected_sales = round(current_sales + current_sales_per_day * multiplier * remaining_days, 2)
        projected_units = round(current_units + current_units_per_day * multiplier * remaining_days, 2)
        gap_pct = _pct_change(projected_sales, objective_sales_close)
        scenarios.append(
            {
                "label": label,
                "paceDeltaPct": pace_delta_pct,
                "projectedSalesClose": projected_sales,
                "projectedUnitsClose": projected_units,
                "gapVsObjectivePct": gap_pct,
                "salesPerDay": round(current_sales_per_day * multiplier, 2),
                "unitsPerDay": round(current_units_per_day * multiplier, 2),
                "tone": _scenario_tone(gap_pct),
            }
        )
    return {
        "remainingDays": remaining_days,
        "currentSalesPerDay": current_sales_per_day,
        "currentUnitsPerDay": current_units_per_day,
        "requiredSalesPerDay": required_sales_per_day,
        "requiredUnitsPerDay": required_units_per_day,
        "scenarios": scenarios,
    }


def _build_role_alerts(summary, sellers, routes, channels, families, key_clients_without_purchase):
    role_alerts = []
    sales_gap = summary["salesVsObjectivePct"]
    projected_gap = _pct_change(summary["projectedSalesClose"], summary["objectiveSalesClose"])

    if sales_gap < -4 or projected_gap < -3:
        role_alerts.append({
            "role": "Dirección",
            "severity": "critical" if projected_gap < -8 else "warn",
            "title": "Riesgo de cierre",
            "text": f"El cierre proyectado viene {abs(projected_gap)}% debajo del objetivo mensual.",
        })
    else:
        role_alerts.append({
            "role": "Dirección",
            "severity": "good",
            "title": "Cierre controlado",
            "text": f"El ritmo actual deja el cierre {projected_gap}% contra el objetivo del mes.",
        })

    weak_sellers = [
        item for item in sellers
        if item["salesGrowthPct"] <= -10 and max(item["sales"], item["previousSales"]) >= summary["salesMTD"] * 0.015
    ]
    if weak_sellers:
        worst = sorted(weak_sellers, key=lambda item: item["salesGrowthPct"])[0]
        role_alerts.append({
            "role": "Jefatura ventas",
            "severity": "warn",
            "title": "Vendedor a corregir",
            "text": f"{worst['label']} cae {abs(worst['salesGrowthPct'])}% y necesita reencauzar cartera este mes.",
        })

    weak_routes = [item for item in routes if item["clientDelta"] <= -6]
    if weak_routes:
        worst = sorted(weak_routes, key=lambda item: item["clientDelta"])[0]
        role_alerts.append({
            "role": "Supervisión",
            "severity": "warn",
            "title": "Ruta debilitada",
            "text": f"{worst['label']} perdió {abs(worst['clientDelta'])} clientes activos vs el mes anterior.",
        })

    weak_channel = next((item for item in sorted(channels, key=lambda row: row["salesGrowthPct"]) if item["salesGrowthPct"] <= -8 and item["clients"] >= 20), None)
    if weak_channel:
        role_alerts.append({
            "role": "Canales",
            "severity": "warn",
            "title": "Canal con baja rotación",
            "text": f"{weak_channel['label']} cae {abs(weak_channel['salesGrowthPct'])}% con {weak_channel['clients']} clientes activos.",
        })

    weak_family = next((item for item in sorted(families, key=lambda row: row["salesGrowthPct"]) if item["salesGrowthPct"] <= -8 and item["sharePct"] >= 5), None)
    if weak_family:
        role_alerts.append({
            "role": "Trade / Compras",
            "severity": "warn",
            "title": "Familia en retracción",
            "text": f"{weak_family['label']} pierde share y cae {abs(weak_family['salesGrowthPct'])}% en el tramo mensual.",
        })

    if key_clients_without_purchase:
        role_alerts.append({
            "role": "Key accounts",
            "severity": "critical" if len(key_clients_without_purchase) >= 20 else "warn",
            "title": "Clientes relevantes sin compra",
            "text": f"{len(key_clients_without_purchase)} cuentas relevantes del mes pasado siguen sin compra este mes.",
        })

    return role_alerts[:6]


def _normalize_planning(planning):
    planning = planning or {}
    focus_dimension = planning.get("focusDimension")
    if focus_dimension not in {"channel", "seller", "family"}:
        focus_dimension = "channel"
    segment_targets = planning.get("segmentTargets") or {}

    def normalize_map(values):
        normalized = {}
        if not isinstance(values, dict):
            return normalized
        for key, value in values.items():
            try:
                normalized[str(key)] = round(float(value), 1)
            except (TypeError, ValueError):
                continue
        return normalized

    try:
        global_target_pct = round(float(planning.get("globalTargetPct", 0) or 0), 1)
    except (TypeError, ValueError):
        global_target_pct = 0.0
    try:
        recovery_goal_clients = int(planning.get("recoveryGoalClients", 0) or 0)
    except (TypeError, ValueError):
        recovery_goal_clients = 0
    return {
        "globalTargetPct": global_target_pct,
        "recoveryGoalClients": max(recovery_goal_clients, 0),
        "focusDimension": focus_dimension,
        "ownerTargets": normalize_map(planning.get("ownerTargets")),
        "segmentTargets": {
            "channel": normalize_map(segment_targets.get("channel")),
            "seller": normalize_map(segment_targets.get("seller")),
            "family": normalize_map(segment_targets.get("family")),
        },
    }


def _build_planning_result(planning, summary, channels, sellers, families, month_window):
    planning = _normalize_planning(planning)
    focus_dimension = planning["focusDimension"]
    source_rows = {
        "channel": channels,
        "seller": sellers,
        "family": families,
    }[focus_dimension]
    dimension_label = {
        "channel": "Canal",
        "seller": "Vendedor",
        "family": "Familia",
    }[focus_dimension]
    target_map = planning["segmentTargets"].get(focus_dimension, {})
    days_elapsed = max(month_window["daysElapsed"], 1)
    days_in_month = max(month_window["daysInMonth"], 1)
    focus_rows = []
    for item in sorted(source_rows, key=lambda row: row["sales"], reverse=True)[:8]:
        uplift_pct = target_map.get(item["label"], 0.0)
        base_projected_sales = round(item["sales"] / days_elapsed * days_in_month, 2)
        target_projected_sales = round(base_projected_sales * (1 + uplift_pct / 100), 2)
        incremental_sales = round(target_projected_sales - base_projected_sales, 2)
        focus_rows.append(
            {
                "dimension": dimension_label,
                "label": item["label"],
                "sharePct": item.get("sharePct", 0.0),
                "sales": round(item["sales"], 2),
                "clients": item.get("clients", 0),
                "growthPct": item.get("salesGrowthPct", 0.0),
                "targetUpliftPct": uplift_pct,
                "baseProjectedSales": base_projected_sales,
                "targetProjectedSales": target_projected_sales,
                "incrementalSales": incremental_sales,
            }
        )
    additional_sales = round(sum(max(row["incrementalSales"], 0.0) for row in focus_rows), 2)
    adjusted_objective_close = round(summary["objectiveSalesClose"] * (1 + planning["globalTargetPct"] / 100), 2)
    simulated_close = round(summary["projectedSalesClose"] + additional_sales, 2)
    gap_vs_adjusted = _pct_change(simulated_close, adjusted_objective_close)
    weighted_uplift_pct = _safe_pct(additional_sales, max(summary["projectedSalesClose"], 1))
    clients_recovery_gap = max(planning["recoveryGoalClients"] - summary["clientsRecovered"], 0)
    insights = [
        f"El simulador está tomando {dimension_label.lower()} como palanca principal para el cierre.",
        f"Con las metas cargadas, el cierre simulado pasa a {simulated_close:,.0f} y queda {gap_vs_adjusted}% vs objetivo ajustado.".replace(",", "."),
        f"La palanca configurada agrega {additional_sales:,.0f} sobre la proyección base del mes.".replace(",", "."),
    ]
    alerts = []
    if adjusted_objective_close > 0 and simulated_close < adjusted_objective_close * 0.98:
        alerts.append({
            "severity": "warn" if simulated_close >= adjusted_objective_close * 0.94 else "critical",
            "text": f"Aun con la simulación activa, el cierre queda {abs(gap_vs_adjusted)}% debajo de la meta ajustada.",
        })
    if clients_recovery_gap > 0:
        alerts.append({
            "severity": "warn",
            "text": f"Faltan {clients_recovery_gap} clientes recuperados para alcanzar la meta definida.",
        })
    return {
        "config": planning,
        "summary": {
            "dimensionLabel": dimension_label,
            "adjustedObjectiveClose": adjusted_objective_close,
            "baseProjectedClose": summary["projectedSalesClose"],
            "simulatedProjectedClose": simulated_close,
            "additionalSales": additional_sales,
            "gapVsAdjustedObjectivePct": gap_vs_adjusted,
            "weightedUpliftPct": weighted_uplift_pct,
            "recoveryGapClients": clients_recovery_gap,
        },
        "kpis": [
            {"label": "Meta global", "value": planning["globalTargetPct"], "format": "pct", "sub": "sobre objetivo estadístico", "tone": "neutral"},
            {"label": "Objetivo ajustado", "value": adjusted_objective_close, "format": "money", "sub": "cierre objetivo recalibrado", "tone": "neutral"},
            {"label": "Cierre simulado", "value": simulated_close, "format": "money", "sub": f"palanca foco: {dimension_label.lower()}", "tone": "good" if simulated_close >= adjusted_objective_close else "warn"},
            {"label": "Gap simulado", "value": gap_vs_adjusted, "format": "pct", "sub": "simulado vs meta ajustada", "tone": "good" if gap_vs_adjusted >= 0 else "bad" if gap_vs_adjusted <= -5 else "warn"},
            {"label": "Venta incremental", "value": additional_sales, "format": "money", "sub": "aporte de metas segmentadas", "tone": "good" if additional_sales > 0 else "neutral"},
            {"label": "Recuperación pendiente", "value": clients_recovery_gap, "format": "int", "sub": "clientes para cumplir meta", "tone": "warn" if clients_recovery_gap > 0 else "good"},
        ],
        "rows": focus_rows,
        "insights": insights,
        "alerts": alerts,
    }


def _build_owner_tracking_result(planning, sellers, month_window):
    planning = _normalize_planning(planning)
    days_elapsed = max(month_window["daysElapsed"], 1)
    days_in_month = max(month_window["daysInMonth"], 1)
    owner_targets = planning.get("ownerTargets", {})
    rows = []
    for item in sorted(sellers, key=lambda row: row["sales"], reverse=True)[:16]:
        seller = item["label"]
        target_pct = owner_targets.get(seller, 0.0)
        projected_sales_close = round(item["sales"] / days_elapsed * days_in_month, 2)
        target_sales_close = round(projected_sales_close * (1 + target_pct / 100), 2)
        gap_value = round(projected_sales_close - target_sales_close, 2)
        gap_pct = _pct_change(projected_sales_close, target_sales_close)
        tone = "good" if gap_pct >= 0 else "warn" if gap_pct >= -5 else "bad"
        rows.append(
            {
                "seller": seller,
                "sales": round(item["sales"], 2),
                "clients": item.get("clients", 0),
                "salesGrowthPct": item.get("salesGrowthPct", 0.0),
                "sharePct": item.get("sharePct", 0.0),
                "targetPct": target_pct,
                "projectedSalesClose": projected_sales_close,
                "targetSalesClose": target_sales_close,
                "gapValue": gap_value,
                "gapPct": gap_pct,
                "tone": tone,
            }
        )
    total_projected = round(sum(item["projectedSalesClose"] for item in rows), 2)
    total_target = round(sum(item["targetSalesClose"] for item in rows), 2)
    total_gap = round(total_projected - total_target, 2)
    on_track = len([item for item in rows if item["gapPct"] >= 0])
    at_risk = len([item for item in rows if item["gapPct"] < -5])
    biggest_gap = sorted(rows, key=lambda item: item["gapValue"])[0] if rows else None
    insights = []
    if rows:
        insights.append(f"{on_track} responsables vienen en línea o arriba del plan mensual.")
        insights.append(f"El desvío agregado contra plan es {total_gap:,.0f} sobre {total_target:,.0f} planificados.".replace(",", "."))
        if biggest_gap:
            insights.append(f"{biggest_gap['seller']} es hoy el mayor desvío contra plan con {abs(biggest_gap['gapPct'])}%.".replace(".", ",", 1))
    alerts = []
    if total_gap < 0:
        alerts.append({
            "severity": "critical" if total_gap <= -50000000 else "warn",
            "text": f"El frente de responsables queda {abs(total_gap):,.0f} por debajo del plan mensual cargado.".replace(",", "."),
        })
    if biggest_gap and biggest_gap["gapPct"] < -8:
        alerts.append({
            "severity": "warn",
            "text": f"{biggest_gap['seller']} se desvía {abs(biggest_gap['gapPct'])}% contra su plan asignado.",
        })
    return {
        "config": {
            "ownerTargets": owner_targets,
        },
        "summary": {
            "trackedOwners": len(rows),
            "onTrackOwners": on_track,
            "atRiskOwners": at_risk,
            "projectedSalesClose": total_projected,
            "targetSalesClose": total_target,
            "gapValue": total_gap,
            "gapPct": _pct_change(total_projected, total_target),
        },
        "kpis": [
            {"label": "Responsables seguidos", "value": len(rows), "format": "int", "sub": "vendedores en tablero", "tone": "neutral"},
            {"label": "En plan", "value": on_track, "format": "int", "sub": "cumplen o superan meta", "tone": "good"},
            {"label": "En riesgo", "value": at_risk, "format": "int", "sub": "desvío mayor a 5%", "tone": "warn" if at_risk else "good"},
            {"label": "Gap plan", "value": total_gap, "format": "money", "sub": "proyectado vs meta cargada", "tone": "good" if total_gap >= 0 else "bad" if total_gap <= -50000000 else "warn"},
        ],
        "charts": {
            "deviation": sorted(rows, key=lambda item: item["gapValue"])[:8],
        },
        "rows": rows,
        "insights": insights[:3],
        "alerts": alerts[:3],
    }


def build_tactical_month_dashboard(filtered_records, selected_end, planning=None):
    month_window = _month_range(selected_end)
    current_records = _records_between(filtered_records, month_window["currentStart"], month_window["currentEnd"])
    previous_mtd_records = _records_between(filtered_records, month_window["previousStart"], month_window["previousEnd"])
    previous_full_records = _records_between(filtered_records, month_window["previousStart"], month_window["previousFullEnd"])
    yoy_mtd_records = _records_between(filtered_records, month_window["yoyStart"], month_window["yoyEnd"])
    history_before_current = [item for item in filtered_records if item["date"] < month_window["currentStart"]]

    current_sales = _sum_amount(current_records)
    current_units = _sum_quantity(current_records)
    prev_mtd_sales = _sum_amount(previous_mtd_records)
    prev_mtd_units = _sum_quantity(previous_mtd_records)
    yoy_mtd_sales = _sum_amount(yoy_mtd_records)
    yoy_mtd_units = _sum_quantity(yoy_mtd_records)

    objective = _build_objective(month_window["currentEnd"], filtered_records)
    objective_sales_close = objective["salesClose"]
    objective_units_close = objective["unitsClose"]
    elapsed_ratio = month_window["daysElapsed"] / max(month_window["daysInMonth"], 1)
    objective_sales_mtd = round(objective_sales_close * elapsed_ratio, 2)
    objective_units_mtd = round(objective_units_close * elapsed_ratio, 2)
    projected_sales_close = round(current_sales / max(month_window["daysElapsed"], 1) * month_window["daysInMonth"], 2)
    projected_units_close = round(current_units / max(month_window["daysElapsed"], 1) * month_window["daysInMonth"], 2)
    sensitivity = _build_sensitivity(
        current_sales,
        current_units,
        month_window["daysElapsed"],
        month_window["daysInMonth"],
        objective_sales_close,
        objective_units_close,
    )

    current_clients = _unique_clients(current_records)
    prev_mtd_clients = _unique_clients(previous_mtd_records)
    prev_full_clients = _unique_clients(previous_full_records)
    historical_clients = _unique_clients(history_before_current)

    clients_without_purchase = len(prev_full_clients - current_clients)
    dropped_vs_prev = len(prev_mtd_clients - current_clients)
    recovered_clients = len((current_clients & historical_clients) - prev_full_clients)
    new_clients = len(current_clients - historical_clients)

    current_orders = len(_unique_orders(current_records))
    ticket_per_client = round(current_sales / max(len(current_clients), 1), 2)
    units_per_client = round(current_units / max(len(current_clients), 1), 2)
    frequency = round(current_orders / max(len(current_clients), 1), 2)

    sellers = _dimension_with_previous(current_records, previous_mtd_records, "seller_name", "Sin vendedor")
    routes = _dimension_with_previous(current_records, previous_mtd_records, "route_description", "Sin ruta")
    channels = _dimension_with_previous(current_records, previous_mtd_records, "channel", "Sin canal")
    suppliers = _dimension_with_previous(current_records, previous_mtd_records, "supplier", "Sin proveedor")
    families = _dimension_with_previous(current_records, previous_mtd_records, "family", "Sin familia")

    top_clients_rows = _aggregate_clients(current_records)
    prev_clients_map = {row["client_id"]: row for row in _aggregate_clients(previous_mtd_records)}
    history_clients_map = {row["client_id"]: row for row in _aggregate_clients(history_before_current)}
    total_current_sales = max(current_sales, 1)
    table_rows = []
    for row in top_clients_rows:
        previous_row = prev_clients_map.get(row["client_id"], {})
        status = "Activo"
        if row["client_id"] not in historical_clients:
            status = "Nuevo"
        elif row["client_id"] not in prev_full_clients:
            status = "Recuperado"
        table_rows.append(
            {
                **row,
                "prevSales": round(previous_row.get("sales", 0.0), 2),
                "salesDeltaPct": _pct_change(row["sales"], previous_row.get("sales", 0.0)),
                "sharePct": _safe_pct(row["sales"], total_current_sales),
                "status": status,
                "historySales": history_clients_map.get(row["client_id"], {}).get("sales", 0.0),
            }
        )
    table_rows.sort(key=lambda item: item["sales"], reverse=True)

    labels = _day_labels(month_window["currentStart"], month_window["currentEnd"])
    objective_sales_daily = [
        round(objective_sales_close * (index + 1) / max(month_window["daysInMonth"], 1), 2)
        for index in range(len(labels))
    ]

    alerts = []
    key_clients_without_purchase = [row for row in sorted(_aggregate_clients(previous_full_records), key=lambda item: item["sales"], reverse=True)[:50] if row["client_id"] not in current_clients]
    objective_gap_pct = abs(_pct_change(current_sales, objective_sales_mtd))
    if current_sales < objective_sales_mtd * 0.99:
        alerts.append({"severity": "critical" if objective_gap_pct >= 5 else "warn", "text": f"El mes viene {objective_gap_pct}% por debajo del ritmo objetivo."})
    if dropped_vs_prev >= 15:
        alerts.append({"severity": "warn", "text": f"Hay {dropped_vs_prev} clientes menos que en el mismo tramo del mes anterior."})
    weak_sellers = [
        item for item in sellers
        if item["salesGrowthPct"] <= -12 and max(item["sales"], item["previousSales"]) >= current_sales * 0.015
    ]
    if weak_sellers:
        worst_seller = sorted(weak_sellers, key=lambda item: item["salesGrowthPct"])[0]
        alerts.append({"severity": "warn", "text": f"{worst_seller['label']} viene {abs(worst_seller['salesGrowthPct'])}% debajo del mismo tramo del mes anterior."})
    weak_routes = [item for item in routes if item["clientDelta"] <= -8]
    if weak_routes:
        worst_route = sorted(weak_routes, key=lambda item: item["clientDelta"])[0]
        alerts.append({"severity": "warn", "text": f"La ruta {worst_route['label']} perdió {abs(worst_route['clientDelta'])} clientes activos vs el mes anterior."})
    weak_suppliers = [item for item in suppliers if item["salesGrowthPct"] <= -10 and item["sales"] > 0]
    if weak_suppliers:
        worst_supplier = sorted(weak_suppliers, key=lambda item: item["salesGrowthPct"])[0]
        alerts.append({"severity": "warn", "text": f"El proveedor {worst_supplier['label']} cae {abs(worst_supplier['salesGrowthPct'])}% en el mes."})
    weak_families = [item for item in families if item["salesGrowthPct"] <= -10 and item["sharePct"] >= 5]
    if weak_families:
        weakest_family = sorted(weak_families, key=lambda item: item["salesGrowthPct"])[0]
        alerts.append({"severity": "warn", "text": f"La familia {weakest_family['label']} pierde participación y cae {abs(weakest_family['salesGrowthPct'])}%."})
    if key_clients_without_purchase:
        alerts.append({"severity": "critical", "text": f"Hay {len(key_clients_without_purchase)} clientes relevantes del mes pasado que todavía no compraron este mes."})

    role_alerts = _build_role_alerts(
        {
            "salesMTD": current_sales,
            "objectiveSalesClose": objective_sales_close,
            "projectedSalesClose": projected_sales_close,
            "salesVsObjectivePct": _pct_change(current_sales, objective_sales_mtd),
        },
        sellers,
        routes,
        channels,
        families,
        key_clients_without_purchase,
    )

    insights = [
        f"El mes acumula {len(current_clients)} clientes compradores con ticket promedio de {ticket_per_client:,.0f} y {units_per_client:,.1f} bultos por cliente.".replace(",", "X").replace(".", ",").replace("X", "."),
        f"La proyección de cierre es {projected_sales_close:,.0f} en ventas y {projected_units_close:,.0f} bultos si se mantiene el ritmo diario.".replace(",", "X").replace(".", ",").replace("X", "."),
        f"Contra el mismo tramo del mes anterior la venta varía {_pct_change(current_sales, prev_mtd_sales)}% y los clientes activos {_pct_change(len(current_clients), len(prev_mtd_clients))}%.".replace(".", ","),
        f"Hay {clients_without_purchase} clientes del mes pasado sin compra en el mes y {recovered_clients} recuperados en la ventana actual.",
    ]

    month_label = _month_label(month_window["currentEnd"])
    planning_result = _build_planning_result(
        planning,
        {
            "objectiveSalesClose": objective_sales_close,
            "projectedSalesClose": projected_sales_close,
            "clientsRecovered": recovered_clients,
        },
        channels,
        sellers,
        families,
        month_window,
    )
    owner_tracking = _build_owner_tracking_result(planning, sellers, month_window)
    return {
        "meta": {
            "monthLabel": month_label,
            "asOfDate": month_window["currentEnd"].isoformat(),
            "startDate": month_window["currentStart"].isoformat(),
            "endDate": month_window["currentEnd"].isoformat(),
            "elapsedDays": month_window["daysElapsed"],
            "daysInMonth": month_window["daysInMonth"],
            "completionPct": round(elapsed_ratio * 100, 1),
        },
        "summary": {
            "salesMTD": current_sales,
            "unitsMTD": current_units,
            "objectiveSalesMTD": objective_sales_mtd,
            "objectiveUnitsMTD": objective_units_mtd,
            "objectiveSalesClose": objective_sales_close,
            "objectiveUnitsClose": objective_units_close,
            "projectedSalesClose": projected_sales_close,
            "projectedUnitsClose": projected_units_close,
            "salesVsObjectivePct": _pct_change(current_sales, objective_sales_mtd),
            "unitsVsObjectivePct": _pct_change(current_units, objective_units_mtd),
            "salesVsPrevMTDPct": _pct_change(current_sales, prev_mtd_sales),
            "salesVsYoYPct": _pct_change(current_sales, yoy_mtd_sales),
            "unitsVsPrevMTDPct": _pct_change(current_units, prev_mtd_units),
            "unitsVsYoYPct": _pct_change(current_units, yoy_mtd_units),
            "activeClients": len(current_clients),
            "clientsWithoutPurchase": clients_without_purchase,
            "clientsDroppedVsPrev": dropped_vs_prev,
            "clientsRecovered": recovered_clients,
            "newClients": new_clients,
            "ticketPerClient": ticket_per_client,
            "unitsPerClient": units_per_client,
            "orders": current_orders,
            "frequency": frequency,
            "projectedGapVsObjectivePct": _pct_change(projected_sales_close, objective_sales_close),
            "currentSalesPerDay": sensitivity["currentSalesPerDay"],
            "currentUnitsPerDay": sensitivity["currentUnitsPerDay"],
            "requiredSalesPerDay": sensitivity["requiredSalesPerDay"],
            "requiredUnitsPerDay": sensitivity["requiredUnitsPerDay"],
            "remainingDays": sensitivity["remainingDays"],
        },
        "kpis": [
            {"label": "Venta acumulada", "value": current_sales, "format": "money", "sub": f"ritmo objetivo {objective_sales_mtd:,.0f}".replace(",", "."), "tone": "good" if current_sales >= objective_sales_mtd else "bad"},
            {"label": "Proyección cierre", "value": projected_sales_close, "format": "money", "sub": f"cierre objetivo {objective_sales_close:,.0f}".replace(",", "."), "tone": "good" if projected_sales_close >= objective_sales_close else "warn"},
            {"label": "Vs objetivo", "value": _pct_change(current_sales, objective_sales_mtd), "format": "pct", "sub": "avance del mes a ritmo esperado", "tone": "good" if current_sales >= objective_sales_mtd else "bad"},
            {"label": "Vs mes anterior", "value": _pct_change(current_sales, prev_mtd_sales), "format": "pct", "sub": "mismo tramo del mes anterior", "tone": "good" if current_sales >= prev_mtd_sales else "warn"},
            {"label": "Vs año anterior", "value": _pct_change(current_sales, yoy_mtd_sales), "format": "pct", "sub": "mismo tramo del año anterior", "tone": "good" if current_sales >= yoy_mtd_sales else "warn"},
            {"label": "Clientes activos", "value": len(current_clients), "format": "int", "sub": f"{clients_without_purchase} sin compra este mes", "tone": "neutral"},
            {"label": "Clientes recuperados", "value": recovered_clients, "format": "int", "sub": f"{dropped_vs_prev} caídos vs mes anterior", "tone": "good" if recovered_clients >= max(dropped_vs_prev, 1) * 0.35 else "warn"},
            {"label": "Ticket por cliente", "value": ticket_per_client, "format": "money", "sub": f"{frequency} pedidos por cliente", "tone": "neutral"},
            {"label": "Bultos por cliente", "value": units_per_client, "format": "number", "sub": f"{current_units:,.0f} bultos del mes".replace(",", "."), "tone": "neutral"},
            {"label": "Bultos vs objetivo", "value": _pct_change(current_units, objective_units_mtd), "format": "pct", "sub": f"proyección {projected_units_close:,.0f} bultos".replace(",", "."), "tone": "good" if current_units >= objective_units_mtd else "warn"},
        ],
        "charts": {
            "dailyProgress": {
                "labels": labels,
                "currentSales": _cumulative_daily(current_records, month_window["currentStart"], month_window["currentEnd"], "amount"),
                "previousSales": _cumulative_daily(previous_mtd_records, month_window["previousStart"], month_window["previousEnd"], "amount"),
                "yoySales": _cumulative_daily(yoy_mtd_records, month_window["yoyStart"], month_window["yoyEnd"], "amount"),
                "objectiveSales": objective_sales_daily,
            },
            "sellers": sorted(sellers, key=lambda item: item["sales"], reverse=True)[:8],
            "routes": sorted(routes, key=lambda item: item["sales"], reverse=True)[:8],
            "channels": sorted(channels, key=lambda item: item["sales"], reverse=True)[:8],
            "families": sorted(families, key=lambda item: item["sales"], reverse=True)[:8],
        },
        "alerts": alerts[:8],
        "roleAlerts": role_alerts,
        "insights": insights[:6],
        "objective": objective,
        "sensitivity": sensitivity,
        "planning": planning_result,
        "ownerTracking": owner_tracking,
        "table": {
            "rows": table_rows[:50],
        },
    }
