from collections import defaultdict


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
    return round(sum(record.get("amount", 0) or 0 for record in records), 2)


def _sum_quantity(records):
    return round(sum(record.get("quantity", 0) or 0 for record in records), 2)


def _unique_orders(records):
    return {record.get("invoice") for record in records if record.get("invoice")}


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
        for record in current_records:
            if _client_id(record):
                client_sales[_client_id(record)] += record.get("amount", 0) or 0
        top_clients_share = _safe_pct(sum(sorted(client_sales.values(), reverse=True)[:3]), current_sales)
        rows.append(
            {
                "seller": label,
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
                "recoveredClients": len(current_clients - previous_clients),
                "droppedClients": len(previous_clients - current_clients),
                "top3ClientsSharePct": top_clients_share,
                "salesForce": _most_common(current_records or previous_records, "sales_force", "Sin fuerza de ventas"),
                "route": _most_common(current_records or previous_records, "route_description", "Sin ruta"),
                "status": "En riesgo" if _pct_change(current_sales, previous_sales) <= -12 or (len(current_clients) - len(previous_clients)) <= -6 else "Sólido" if _pct_change(current_sales, previous_sales) >= 5 and len(current_clients) >= len(previous_clients) else "Atento",
            }
        )
    rows.sort(key=lambda item: item["sales"], reverse=True)
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
    return {
        "summary": {
            "sellerCount": len(active_rows),
            "salesPerSeller": round(total_sales / max(len(active_rows), 1), 2),
            "unitsPerSeller": round(_sum_quantity(current_period) / max(len(active_rows), 1), 2),
            "clientsPerSeller": round(sum(item["clients"] for item in active_rows) / max(len(active_rows), 1), 1),
            "top3SharePct": concentration,
            "underperformingCount": len(weak_rows),
        },
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
        "rows": rows[:50],
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
    total_sales = sum(sum(item.get("sales12m", 0) or 0 for item in group) for group in grouped_stats.values())
    for canonical_id, stats_group in grouped_stats.items():
        sample = sorted(stats_group, key=lambda item: item.get("sales12m", 0) or 0, reverse=True)[0]
        status = sorted((item.get("status") or "Activo" for item in stats_group), key=_status_rank)[0]
        current_sales = sum(item.get("sales12m", 0) or 0 for item in stats_group)
        previous_sales = sum(item.get("salesPrevious", 0) or 0 for item in stats_group)
        sales_history = sum(item.get("salesHistory", item.get("sales12m", 0) or 0) or 0 for item in stats_group)
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
                "clientKey": client_id,
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
