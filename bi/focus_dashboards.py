from collections import defaultdict
from statistics import mean, pstdev

from sales_coach.domain import CoachCommentEngine, DEFAULT_SELLER_RULES


def _safe_pct(part, total):
    return round((part / max(total, 1)) * 100, 1)


def _pct_change(current, previous):
    if abs(previous) < 0.0001:
        if abs(current) < 0.0001:
            return 0.0
        return 100.0
    return round(((current - previous) / abs(previous)) * 100, 1)


def _client_id(record):
    return record.get("client_canonical") or record.get("client_key") or record.get("client")


def _most_common(records, field, fallback):
    counts = defaultdict(int)
    for record in records:
        value = record.get(field) or fallback
        counts[value] += 1
    if not counts:
        return fallback
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)[0][0]


def _group_records(records, key_field, missing_label):
    grouped = defaultdict(list)
    for record in records:
        grouped[record.get(key_field) or missing_label].append(record)
    return grouped


def _sum_amount(records):
    return round(
        sum(
            record.get("amount_net")
            if record.get("amount_net") is not None
            else record.get("amount", 0) or 0
            for record in records
        ),
        2,
    )


def _sum_quantity(records):
    return round(sum(record.get("quantity", 0) or 0 for record in records), 2)


def _unique_orders(records):
    return {record.get("invoice") for record in records if record.get("invoice")}


def _commercial_line(record):
    return (
        record.get("line")
        or record.get("family")
        or record.get("brand")
        or "Sin línea"
    )


def _line_totals(records):
    totals = defaultdict(lambda: {"sales": 0.0, "quantity": 0.0})
    for record in records:
        bucket = totals[_commercial_line(record)]
        bucket["sales"] += (
            record.get("amount_net")
            if record.get("amount_net") is not None
            else record.get("amount", 0) or 0
        )
        bucket["quantity"] += record.get("quantity", 0) or 0
    return totals


def _status_rank(status):
    return {"Perdido": 0, "Reactivable": 1, "Dormido": 2, "Activo": 3}.get(status, 4)


def _normalize_text(value):
    text = str(value or "").strip().lower()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
        "ñ": "n",
    }
    for src, target in replacements.items():
        text = text.replace(src, target)
    return " ".join(text.split())


def build_focus_dashboards(current_period, previous_period, client_stats, opportunities, period_context):
    return {
        "sellers": build_sellers_dashboard(current_period, previous_period, period_context),
        "clients": build_clients_dashboard(current_period, previous_period, client_stats, opportunities, period_context),
    }


def build_sellers_dashboard(current_period, previous_period, period_context):
    current_grouped = _group_records(current_period, "seller_name", "Sin vendedor")
    previous_grouped = _group_records(previous_period, "seller_name", "Sin vendedor")
    labels = set(current_grouped) | set(previous_grouped)
    rows = []
    total_sales = _sum_amount(current_period)
    total_quantity = _sum_quantity(current_period)
    team_line_current = _line_totals(current_period)
    team_line_previous = _line_totals(previous_period)
    visible_lines = sorted(
        set(team_line_current) | set(team_line_previous),
        key=lambda line: team_line_current[line]["sales"],
        reverse=True,
    )
    for label in labels:
        current_records = current_grouped.get(label, [])
        previous_records = previous_grouped.get(label, [])
        current_sales = _sum_amount(current_records)
        previous_sales = _sum_amount(previous_records)
        current_quantity = _sum_quantity(current_records)
        current_clients = {_client_id(item) for item in current_records if _client_id(item)}
        previous_clients = {_client_id(item) for item in previous_records if _client_id(item)}
        orders = _unique_orders(current_records)
        client_sales = defaultdict(float)
        client_quantities = defaultdict(float)
        client_names = {}
        previous_client_sales = defaultdict(float)
        for record in current_records:
            if _client_id(record):
                client_key = _client_id(record)
                client_sales[client_key] += (
                    record.get("amount_net")
                    if record.get("amount_net") is not None
                    else record.get("amount", 0) or 0
                )
                client_quantities[client_key] += record.get("quantity", 0) or 0
                client_names[client_key] = (
                    record.get("client_name")
                    or record.get("client")
                    or client_key
                )
        for record in previous_records:
            if _client_id(record):
                client_key = _client_id(record)
                previous_client_sales[client_key] += (
                    record.get("amount_net")
                    if record.get("amount_net") is not None
                    else record.get("amount", 0) or 0
                )
                client_names.setdefault(
                    client_key,
                    record.get("client_name") or record.get("client") or client_key,
                )
        top_clients_share = _safe_pct(sum(sorted(client_sales.values(), reverse=True)[:3]), current_sales)
        top_clients = [
            {
                "clientKey": client_key,
                "client": client_names.get(client_key, client_key),
                "sales": round(sales, 2),
                "previousSales": round(previous_client_sales.get(client_key, 0), 2),
                "growthPct": _pct_change(
                    sales, previous_client_sales.get(client_key, 0)
                ),
                "quantity": round(client_quantities.get(client_key, 0), 2),
                "sharePct": _safe_pct(sales, current_sales),
            }
            for client_key, sales in sorted(
                client_sales.items(), key=lambda item: item[1], reverse=True
            )[:10]
        ]
        mix_values = {
            _commercial_line(item)
            for item in current_records
            if _commercial_line(item) != "Sin línea"
        }
        monthly_sales = defaultdict(float)
        for record in current_records:
            sale_date = record.get("date")
            month_key = sale_date.strftime("%Y-%m") if hasattr(sale_date, "strftime") else str(sale_date or "")[:7]
            monthly_sales[month_key] += (
                record.get("amount_net")
                if record.get("amount_net") is not None
                else record.get("amount", 0) or 0
            )
        monthly_values = list(monthly_sales.values())
        variation = (
            pstdev(monthly_values) / max(mean(monthly_values), 1) * 100
            if len(monthly_values) > 1
            else 0
        )
        category_current = defaultdict(float)
        category_previous = defaultdict(float)
        for record in current_records:
            category = _commercial_line(record)
            category_current[category] += (
                record.get("amount_net")
                if record.get("amount_net") is not None
                else record.get("amount", 0) or 0
            )
        for record in previous_records:
            category = _commercial_line(record)
            category_previous[category] += (
                record.get("amount_net")
                if record.get("amount_net") is not None
                else record.get("amount", 0) or 0
            )
        strong_category, strong_value = max(
            category_current.items(), key=lambda item: item[1], default=("", 0)
        )
        category_growth = [
            (
                category,
                _pct_change(category_current.get(category, 0), previous_value),
            )
            for category, previous_value in category_previous.items()
            if previous_value > 0
        ]
        weak_category, weak_growth = min(
            category_growth, key=lambda item: item[1], default=("", None)
        )
        seller_line_current = _line_totals(current_records)
        seller_line_previous = _line_totals(previous_records)
        line_mix = []
        for line in visible_lines:
            current_line = seller_line_current[line]
            previous_line = seller_line_previous[line]
            team_line = team_line_current[line]
            line_mix.append(
                {
                    "line": line,
                    "sales": round(current_line["sales"], 2),
                    "previousSales": round(previous_line["sales"], 2),
                    "growthPct": _pct_change(current_line["sales"], previous_line["sales"]),
                    "quantity": round(current_line["quantity"], 2),
                    "mixPct": _safe_pct(current_line["sales"], current_sales),
                    "teamMixPct": _safe_pct(team_line["sales"], total_sales),
                    "valuePerQuantity": round(
                        current_line["sales"] / max(current_line["quantity"], 1), 2
                    ),
                }
            )
        opportunity_line = min(
            line_mix,
            key=lambda item: item["mixPct"] - item["teamMixPct"],
            default=None,
        )
        strong_team_share = _safe_pct(
            team_line_current[strong_category]["sales"], total_sales
        )
        rows.append(
            {
                "seller": label,
                "sellerKey": _most_common(current_records or previous_records, "seller_key", ""),
                "sales": current_sales,
                "previousSales": previous_sales,
                "growthPct": _pct_change(current_sales, previous_sales),
                "quantity": current_quantity,
                "clients": len(current_clients),
                "clientDelta": len(current_clients) - len(previous_clients),
                "orders": len(orders),
                "avgTicket": round(current_sales / max(len(orders), 1), 2),
                "avgUnitsPerOrder": round(current_quantity / max(len(orders), 1), 2),
                "salesPerClient": round(current_sales / max(len(current_clients), 1), 2),
                "sharePct": _safe_pct(current_sales, total_sales),
                "mixCount": len(mix_values),
                "recoveredClients": len(current_clients - previous_clients),
                "droppedClients": len(previous_clients - current_clients),
                "newClients": len(current_clients - previous_clients),
                "clientsWithoutPurchase": len(previous_clients - current_clients),
                "topClients": top_clients,
                "top3ClientsSharePct": top_clients_share,
                "incrementalSales": round(current_sales - previous_sales, 2),
                "stabilityPct": round(max(0, 100 - variation), 1),
                "strongCategory": strong_category or None,
                "strongCategorySharePct": _safe_pct(strong_value, current_sales),
                "strongCategoryTeamSharePct": strong_team_share,
                "strongCategoryGapPct": round(
                    _safe_pct(strong_value, current_sales) - strong_team_share, 1
                ),
                "weakCategory": (
                    opportunity_line["line"] if opportunity_line else weak_category
                ) or None,
                "weakCategoryGrowthPct": (
                    opportunity_line["growthPct"] if opportunity_line else weak_growth
                ),
                "weakCategorySharePct": (
                    opportunity_line["mixPct"] if opportunity_line else None
                ),
                "weakCategoryTeamSharePct": (
                    opportunity_line["teamMixPct"] if opportunity_line else None
                ),
                "weakCategoryGapPct": (
                    round(
                        opportunity_line["mixPct"] - opportunity_line["teamMixPct"],
                        1,
                    )
                    if opportunity_line
                    else None
                ),
                "salesForce": _most_common(current_records or previous_records, "sales_force", "Sin fuerza de ventas"),
                "route": _most_common(current_records or previous_records, "route_description", "Sin ruta"),
                "valuePerQuantity": round(current_sales / max(current_quantity, 1), 2),
                "lineMix": line_mix,
                "status": "En riesgo" if _pct_change(current_sales, previous_sales) <= -12 or (len(current_clients) - len(previous_clients)) <= -6 else "Sólido" if _pct_change(current_sales, previous_sales) >= 5 and len(current_clients) >= len(previous_clients) else "Atento",
            }
        )
    rows.sort(key=lambda item: item["sales"], reverse=True)
    active_for_benchmark = [row for row in rows if row["seller"] != "Sin vendedor"]
    avg_sales_per_client = (
        sum(row["salesPerClient"] for row in active_for_benchmark)
        / max(len(active_for_benchmark), 1)
    )
    team_growth = (
        sum(row["growthPct"] for row in active_for_benchmark)
        / max(len(active_for_benchmark), 1)
    )
    for row in rows:
        row["potentialEstimated"] = round(
            max(avg_sales_per_client - row["salesPerClient"], 0)
            * row["clients"]
            * 0.25,
            2,
        )
    _apply_separate_ranks(rows, "sales", "rankSales")
    _apply_separate_ranks(rows, "quantity", "rankQuantity")
    _apply_separate_ranks(rows, "growthPct", "rankGrowth")
    _apply_separate_ranks(rows, "clients", "rankActiveClients")
    for line in visible_lines:
        line_rows = sorted(
            rows,
            key=lambda item: next(
                (entry["sales"] for entry in item["lineMix"] if entry["line"] == line),
                0,
            ),
            reverse=True,
        )
        rank = 0
        for item in line_rows:
            entry = next(entry for entry in item["lineMix"] if entry["line"] == line)
            if entry["sales"] > 0:
                rank += 1
                entry["rank"] = rank
            else:
                entry["rank"] = None
    previous_order = sorted(
        [row for row in rows if row.get("seller") != "Sin vendedor"],
        key=lambda item: item.get("previousSales", 0),
        reverse=True,
    )
    for index, row in enumerate(previous_order, start=1):
        row["previousRankSales"] = index
        row["rankDelta"] = index - row.get("rankSales", index)
    context = {
        "teamGrowthPct": team_growth,
        "teamMixAverage": sum(row["mixCount"] for row in active_for_benchmark) / max(len(active_for_benchmark), 1),
        "teamClientsAverage": sum(row["clients"] for row in active_for_benchmark) / max(len(active_for_benchmark), 1),
        "teamTicketAverage": sum(row["avgTicket"] for row in active_for_benchmark) / max(len(active_for_benchmark), 1),
        "teamQuantityAverage": sum(row["quantity"] for row in active_for_benchmark) / max(len(active_for_benchmark), 1),
    }
    comment_engine = CoachCommentEngine(DEFAULT_SELLER_RULES)
    for row in rows:
        row["clientBalance"] = row["recoveredClients"] - row["droppedClients"]
        evaluation = comment_engine.evaluate(row, context)
        row["strengths"] = evaluation["strengths"]
        row["opportunities"] = evaluation["opportunities"]
        row["actionPlan"] = evaluation["actionPlan"]
        row["commentRuleSetVersion"] = 1
    top_rows = rows[:12]
    active_rows = [row for row in rows if row["seller"] != "Sin vendedor"]
    weak_rows = [row for row in active_rows if row["growthPct"] <= -12 and row["sales"] >= total_sales * 0.015]
    concentration = _safe_pct(sum(item["sales"] for item in active_rows[:3]), total_sales)
    insights = [
        f"Los 3 principales vendedores explican {concentration}% de la venta del período.",
        f"El vendedor líder aporta {top_rows[0]['sharePct']}% y atiende {top_rows[0]['clients']} clientes." if top_rows else "No hay vendedores activos en el período.",
        f"Hay {len(weak_rows)} vendedores con caída relevante contra {period_context['comparisonLabel'].lower()}.",
    ]
    alerts = []
    if concentration >= 55:
        alerts.append({"severity": "warn", "text": f"La venta depende {concentration}% de solo 3 vendedores."})
    if weak_rows:
        worst = sorted(weak_rows, key=lambda item: item["growthPct"])[0]
        alerts.append({"severity": "warn", "text": f"{worst['seller']} cae {abs(worst['growthPct'])}% y pierde {abs(worst['clientDelta'])} clientes."})
    low_coverage = [row for row in active_rows if row["clients"] <= 15 and row["sales"] < total_sales * 0.01]
    if low_coverage:
        alerts.append({"severity": "warn", "text": f"Hay {len(low_coverage)} vendedores con cartera liviana o poco desarrollada."})
    team_line_mix = [
        {
            "line": line,
            "sales": round(team_line_current[line]["sales"], 2),
            "previousSales": round(team_line_previous[line]["sales"], 2),
            "growthPct": _pct_change(
                team_line_current[line]["sales"], team_line_previous[line]["sales"]
            ),
            "quantity": round(team_line_current[line]["quantity"], 2),
            "mixPct": _safe_pct(team_line_current[line]["sales"], total_sales),
            "valuePerQuantity": round(
                team_line_current[line]["sales"]
                / max(team_line_current[line]["quantity"], 1),
                2,
            ),
        }
        for line in visible_lines
    ]
    return {
        "summary": {
            "sellerCount": len(active_rows),
            "sales": total_sales,
            "previousSales": _sum_amount(previous_period),
            "growthPct": _pct_change(total_sales, _sum_amount(previous_period)),
            "quantity": total_quantity,
            "valuePerQuantity": round(total_sales / max(total_quantity, 1), 2),
            "salesPerSeller": round(total_sales / max(len(active_rows), 1), 2),
            "unitsPerSeller": round(_sum_quantity(current_period) / max(len(active_rows), 1), 2),
            "clientsPerSeller": round(sum(item["clients"] for item in active_rows) / max(len(active_rows), 1), 1),
            "top3SharePct": concentration,
            "underperformingCount": len(weak_rows),
        },
        "lineMix": team_line_mix,
        "kpis": [
            {"label": "Vendedores activos", "value": len(active_rows), "format": "int", "sub": f"{len(weak_rows)} con caída relevante", "tone": "warn" if weak_rows else "good"},
            {"label": "Venta / vendedor", "value": round(total_sales / max(len(active_rows), 1), 2), "format": "money", "sub": "productividad promedio", "tone": "neutral"},
            {"label": "Clientes / vendedor", "value": round(sum(item['clients'] for item in active_rows) / max(len(active_rows), 1), 1), "format": "number", "sub": "cobertura promedio", "tone": "neutral"},
            {"label": "Top 3 share", "value": concentration, "format": "pct", "sub": "dependencia en pocos vendedores", "tone": "bad" if concentration >= 55 else "warn" if concentration >= 45 else "good"},
            {"label": "Recuperados", "value": sum(item["recoveredClients"] for item in active_rows), "format": "int", "sub": "clientes reactivados por vendedores", "tone": "good"},
            {"label": "Caídos", "value": sum(item["droppedClients"] for item in active_rows), "format": "int", "sub": "clientes perdidos en la comparación", "tone": "warn"},
        ],
        "charts": {
            "topSales": top_rows[:8],
            "growth": sorted(active_rows, key=lambda item: item["growthPct"])[:8] + sorted(active_rows, key=lambda item: item["growthPct"], reverse=True)[:4],
            "clients": sorted(active_rows, key=lambda item: item["clients"], reverse=True)[:8],
            "concentration": sorted(active_rows, key=lambda item: item["top3ClientsSharePct"], reverse=True)[:8],
        },
        "rows": rows,
        "rankingDefinitions": [
            {"code": "rankSales", "label": "Venta", "formula": "orden descendente de venta neta del período"},
            {"code": "rankQuantity", "label": "Cantidad", "formula": "orden descendente de cantidad del período"},
            {"code": "rankGrowth", "label": "Crecimiento", "formula": "(venta actual - venta comparativa) / |venta comparativa|"},
            {"code": "rankActiveClients", "label": "Clientes activos", "formula": "orden descendente de clientes únicos con compra"},
        ],
        "insights": insights[:4],
        "alerts": alerts[:4],
    }


def build_clients_dashboard(current_period, previous_period, client_stats, opportunities, period_context):
    current_clients = {_client_id(item) for item in current_period if _client_id(item)}
    previous_clients = {_client_id(item) for item in previous_period if _client_id(item)}
    grouped_stats = defaultdict(list)
    for item in client_stats.values():
        grouped_stats[_normalize_text(item.get("client") or item.get("client_key"))].append(item)
    rows = []
    status_counts = defaultdict(int)
    recovered_count = 0
    new_count = 0
    total_sales = sum(sum(item.get("salesNet", item.get("sales12m", 0)) or 0 for item in group) for group in grouped_stats.values())
    for canonical_id, stats_group in grouped_stats.items():
        sample = sorted(stats_group, key=lambda item: item.get("sales12m", 0) or 0, reverse=True)[0]
        status = sorted((item.get("status") or "Activo" for item in stats_group), key=_status_rank)[0]
        current_sales = sum(item.get("salesNet", item.get("sales12m", 0)) or 0 for item in stats_group)
        previous_sales = sum(item.get("salesNetPrevious", item.get("salesPrevious", 0)) or 0 for item in stats_group)
        sales_history = sum(item.get("salesNetHistory", item.get("salesHistory", item.get("sales12m", 0))) or 0 for item in stats_group)
        client_id = canonical_id
        lifecycle = status
        if client_id in current_clients and previous_sales <= 0:
            if sales_history > current_sales:
                lifecycle = "Recuperado"
                recovered_count += 1
            else:
                lifecycle = "Nuevo"
                new_count += 1
        status_counts[lifecycle] += 1
        rows.append(
            {
                "client": sample.get("client"),
                "clientKey": sample.get("client_key") or client_id,
                "status": lifecycle,
                "sales": current_sales,
                "previousSales": previous_sales,
                "growthPct": _pct_change(current_sales, previous_sales),
                "quantity": sum(item.get("quantity12m", 0) or 0 for item in stats_group),
                "avgTicket": round(current_sales / max(sum(item.get("orders", 0) or 0 for item in stats_group), 1), 2),
                "avgUnitsPerOrder": round(sum(item.get("quantity12m", 0) or 0 for item in stats_group) / max(sum(item.get("orders", 0) or 0 for item in stats_group), 1), 2),
                "families": max(item.get("families", 0) or 0 for item in stats_group),
                "lastDate": max((item.get("lastDate") or "" for item in stats_group), default=""),
                "salesForce": _most_common(stats_group, "sales_force", "Sin fuerza de ventas"),
                "route": _most_common(stats_group, "route_description", "Sin ruta"),
                "seller": _most_common(stats_group, "seller_name", "Sin vendedor"),
                "recencyDays": min(item.get("recencyDays", 0) or 0 for item in stats_group),
                "sharePct": _safe_pct(current_sales, total_sales),
                "products": max(item.get("products", 0) or 0 for item in stats_group),
            }
        )
    rows.sort(key=lambda item: item["sales"], reverse=True)
    active_rows = [row for row in rows if row["status"] in {"Activo", "Recuperado", "Nuevo"} and row["sales"] > 0]
    risk_rows = [row for row in rows if row["status"] in {"Dormido", "Reactivable", "Perdido"}]
    top10_share = _safe_pct(sum(item["sales"] for item in rows[:10]), total_sales)
    clients_without_purchase = len(previous_clients - current_clients)
    insights = [
        f"El top 10 de clientes concentra {top10_share}% de la venta del período.",
        f"Hay {clients_without_purchase} clientes del período comparativo que todavía no repitieron compra.",
        f"Se detectan {recovered_count} clientes recuperados y {new_count} nuevos en la ventana actual.",
        f"{len(risk_rows)} clientes están en estado dormido, reactivable o perdido.",
    ]
    alerts = []
    if top10_share >= 45:
        alerts.append({"severity": "warn", "text": f"La cartera concentra {top10_share}% en solo 10 cuentas."})
    if clients_without_purchase >= 120:
        alerts.append({"severity": "warn", "text": f"Hay {clients_without_purchase} clientes del período anterior sin recompra."})
    if opportunities.get("lowBreadthClients", 0) >= 25:
        alerts.append({"severity": "warn", "text": f"{opportunities['lowBreadthClients']} clientes activos compran 1 familia o menos."})
    largest_risk = sorted(risk_rows, key=lambda item: item["sales"], reverse=True)[:1]
    if largest_risk:
        alerts.append({"severity": "critical", "text": f"{largest_risk[0]['client']} está en {largest_risk[0]['status']} con {largest_risk[0]['recencyDays']} días sin compra."})
    return {
        "summary": {
            "activeCount": len(active_rows),
            "riskCount": len(risk_rows),
            "recoveredCount": recovered_count,
            "newCount": new_count,
            "top10SharePct": top10_share,
            "avgTicket": round(sum(item["avgTicket"] for item in active_rows) / max(len(active_rows), 1), 2),
        },
        "kpis": [
            {"label": "Clientes activos", "value": len(active_rows), "format": "int", "sub": f"{clients_without_purchase} sin recompra", "tone": "neutral"},
            {"label": "Recuperados", "value": recovered_count, "format": "int", "sub": "clientes que volvieron a comprar", "tone": "good"},
            {"label": "Nuevos", "value": new_count, "format": "int", "sub": "clientes sin historia previa", "tone": "good"},
            {"label": "En riesgo", "value": len(risk_rows), "format": "int", "sub": "dormidos, reactivables o perdidos", "tone": "warn"},
            {"label": "Top 10 share", "value": top10_share, "format": "pct", "sub": "concentración de cuentas clave", "tone": "bad" if top10_share >= 50 else "warn" if top10_share >= 40 else "good"},
            {"label": "Ticket activo", "value": round(sum(item["avgTicket"] for item in active_rows) / max(len(active_rows), 1), 2), "format": "money", "sub": f"{opportunities.get('lowBreadthClients', 0)} con bajo mix", "tone": "neutral"},
        ],
        "charts": {
            "status": [{"label": label, "value": value} for label, value in sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))],
            "topSales": rows[:8],
            "risk": sorted(risk_rows, key=lambda item: item["sales"], reverse=True)[:8],
            "mix": sorted(active_rows, key=lambda item: item["families"])[:8],
        },
        "rows": rows[:60],
        "insights": insights[:4],
        "alerts": alerts[:4],
    }


def _apply_separate_ranks(rows, metric, target):
    ordered = sorted(
        [row for row in rows if row.get("seller") != "Sin vendedor"],
        key=lambda item: item.get(metric, 0),
        reverse=True,
    )
    for index, row in enumerate(ordered, start=1):
        row[target] = index
