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


def _sum_amount(records):
    return round(sum(record.get("amount", 0) or 0 for record in records), 2)


def _sum_quantity(records):
    return round(sum(record.get("quantity", 0) or 0 for record in records), 2)


def _rolling_average(values, window):
    output = []
    for index in range(len(values)):
        bucket = values[max(0, index - window + 1): index + 1]
        output.append(round(sum(bucket) / max(len(bucket), 1), 2))
    return output


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


def _group_by_period(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[record["period"]].append(record)
    return dict(sorted(grouped.items()))


def _dimension_share(records, field):
    grouped = defaultdict(float)
    total = _sum_amount(records)
    for record in records:
        grouped[record.get(field) or "Sin dato"] += record.get("amount", 0) or 0
    return sorted(
        [
            {
                "label": label,
                "sales": round(value, 2),
                "sharePct": _safe_pct(value, total),
            }
            for label, value in grouped.items()
        ],
        key=lambda item: item["sales"],
        reverse=True,
    )


def _hhi(records, field):
    shares = [item["sharePct"] / 100 for item in _dimension_share(records, field)]
    return round(sum(share * share for share in shares) * 10000, 0)


def _pareto_clients(records):
    grouped = defaultdict(float)
    total = _sum_amount(records)
    for record in records:
        client = record.get("client") or record.get("client_name") or record.get("client_key") or "Sin cliente"
        grouped[client] += record.get("amount", 0) or 0
    rows = []
    cumulative = 0.0
    for index, (client, sales) in enumerate(sorted(grouped.items(), key=lambda item: item[1], reverse=True), start=1):
        share = _safe_pct(sales, total)
        cumulative = round(cumulative + share, 1)
        rows.append(
            {
                "rank": index,
                "client": client,
                "sales": round(sales, 2),
                "sharePct": share,
                "cumulativePct": min(cumulative, 100.0),
            }
        )
    return rows


def _cohort_rows(records_by_period):
    periods = list(records_by_period.keys())
    seen_before = set()
    previous_period_clients = set()
    rows = []
    for period in periods:
        current_clients = {_client_id(record) for record in records_by_period[period] if _client_id(record)}
        new_clients = current_clients - seen_before
        recovered = (current_clients - previous_period_clients) & seen_before
        active = current_clients & previous_period_clients
        lost = previous_period_clients - current_clients
        rows.append(
            {
                "label": period,
                "new": len(new_clients),
                "active": len(active),
                "recovered": len(recovered),
                "lost": len(lost),
                "currentClients": len(current_clients),
            }
        )
        seen_before |= current_clients
        previous_period_clients = current_clients
    return rows


def _current_period(records, period_context):
    end_date = period_context["selectedEnd"]
    return [record for record in records if record["date"] <= end_date]


def _normalize_budget(planning):
    budget = ((planning or {}).get("budget") or {}) if isinstance(planning, dict) else {}
    dimension = budget.get("dimension")
    if dimension not in {"total", "channel", "seller", "family"}:
        dimension = "total"

    def normalize_amount_map(values):
        normalized = {}
        if not isinstance(values, dict):
            return normalized
        for key, value in values.items():
            try:
                normalized[str(key)] = round(float(value), 2)
            except (TypeError, ValueError):
                continue
        return normalized

    def normalize_share_map(values):
        normalized = {}
        if not isinstance(values, dict):
            return normalized
        for key, value in values.items():
            try:
                normalized[str(key)] = round(float(value), 1)
            except (TypeError, ValueError):
                continue
        return normalized

    shares = budget.get("dimensionShares") or {}
    return {
        "dimension": dimension,
        "monthlyTotals": normalize_amount_map(budget.get("monthlyTotals")),
        "dimensionShares": {
            "channel": normalize_share_map(shares.get("channel")),
            "seller": normalize_share_map(shares.get("seller")),
            "family": normalize_share_map(shares.get("family")),
        },
    }


def _budget_field(dimension):
    return {
        "channel": "channel",
        "seller": "seller_name",
        "family": "family",
    }.get(dimension)


def _build_budget_block(monthly_rows, records_by_period, planning):
    budget = _normalize_budget(planning)
    monthly_totals = budget["monthlyTotals"]
    latest_label = monthly_rows[-1]["label"] if monthly_rows else "-"
    monthly_budget_rows = []
    for row in monthly_rows:
        budget_total = round(monthly_totals.get(row["label"], 0.0), 2)
        gap_value = round(row["sales"] - budget_total, 2)
        monthly_budget_rows.append(
            {
                **row,
                "budgetTotal": budget_total,
                "gapValue": gap_value,
                "gapPct": _pct_change(row["sales"], budget_total),
            }
        )

    latest_budget_row = monthly_budget_rows[-1] if monthly_budget_rows else {"sales": 0, "budgetTotal": 0, "gapValue": 0, "gapPct": 0, "label": "-"}
    months_budgeted = len([row for row in monthly_budget_rows if row["budgetTotal"] > 0])
    total_actual = round(sum(row["sales"] for row in monthly_budget_rows), 2)
    total_budget = round(sum(row["budgetTotal"] for row in monthly_budget_rows), 2)
    total_gap = round(total_actual - total_budget, 2)

    dimension_rows = []
    base_dimension_rows = []
    if budget["dimension"] == "total":
        dimension_rows = [{
            "label": "Total",
            "actualSales": latest_budget_row["sales"],
            "actualSharePct": 100.0 if latest_budget_row["sales"] else 0.0,
            "budgetSharePct": 100.0 if latest_budget_row["budgetTotal"] else 0.0,
            "budgetSales": latest_budget_row["budgetTotal"],
            "gapValue": latest_budget_row["gapValue"],
            "gapPct": latest_budget_row["gapPct"],
        }]
        base_dimension_rows = dimension_rows
    else:
        field = _budget_field(budget["dimension"])
        latest_records = records_by_period.get(latest_label, [])
        actual_rows = _dimension_share(latest_records, field) if field else []
        actual_map = {item["label"]: item for item in actual_rows}
        share_map = budget["dimensionShares"].get(budget["dimension"], {})
        labels = []
        for item in actual_rows[:12]:
            labels.append(item["label"])
        for label in share_map.keys():
            if label not in labels:
                labels.append(label)
        for label in labels:
            actual_sales = round((actual_map.get(label) or {}).get("sales", 0.0), 2)
            actual_share = round((actual_map.get(label) or {}).get("sharePct", 0.0), 1)
            budget_share = round(share_map.get(label, 0.0), 1)
            budget_sales = round(latest_budget_row["budgetTotal"] * budget_share / 100, 2)
            gap_value = round(actual_sales - budget_sales, 2)
            row = {
                "label": label,
                "actualSales": actual_sales,
                "actualSharePct": actual_share,
                "budgetSharePct": budget_share,
                "budgetSales": budget_sales,
                "gapValue": gap_value,
                "gapPct": _pct_change(actual_sales, budget_sales),
            }
            dimension_rows.append(row)
        dimension_rows.sort(key=lambda item: max(item["actualSales"], item["budgetSales"]), reverse=True)
        base_dimension_rows = [
            {
                "label": row["label"],
                "actualSales": row["actualSales"],
                "actualSharePct": row["actualSharePct"],
                "budgetSharePct": row["budgetSharePct"],
                "budgetSales": row["budgetSales"],
            }
            for row in dimension_rows
        ]

    insights = []
    alerts = []
    if months_budgeted:
        insights.append(f"Hay presupuesto cargado para {months_budgeted} meses del histórico visible.")
        insights.append(f"El último período ({latest_budget_row['label']}) cierra {latest_budget_row['gapPct']}% contra plan.")
        insights.append(f"El acumulado visible va {total_gap:,.0f} contra un plan de {total_budget:,.0f}.".replace(",", "."))
        if latest_budget_row["gapPct"] <= -6:
            alerts.append({"severity": "warn", "text": f"El último mes queda {abs(latest_budget_row['gapPct'])}% debajo del presupuesto."})
        if total_gap < 0:
            alerts.append({"severity": "warn", "text": f"El acumulado del rango queda {abs(total_gap):,.0f} por debajo del plan visible.".replace(",", ".")})
    else:
        insights.append("Todavía no hay presupuesto cargado para comparar el histórico contra plan.")

    return {
        "config": budget,
        "summary": {
            "monthsBudgeted": months_budgeted,
            "latestBudget": latest_budget_row["budgetTotal"],
            "latestGapValue": latest_budget_row["gapValue"],
            "latestGapPct": latest_budget_row["gapPct"],
            "totalBudget": total_budget,
            "totalGapValue": total_gap,
            "totalGapPct": _pct_change(total_actual, total_budget),
            "dimension": budget["dimension"],
            "latestPeriod": latest_label,
        },
        "kpis": [
            {"label": "Meses con plan", "value": months_budgeted, "format": "int", "sub": "histórico visible", "tone": "neutral"},
            {"label": "Plan último mes", "value": latest_budget_row["budgetTotal"], "format": "money", "sub": latest_budget_row["label"], "tone": "neutral"},
            {"label": "Gap último mes", "value": latest_budget_row["gapPct"], "format": "pct", "sub": "real vs plan", "tone": "good" if latest_budget_row["gapPct"] >= 0 else "warn"},
            {"label": "Gap acumulado", "value": total_gap, "format": "money", "sub": "real vs plan visible", "tone": "good" if total_gap >= 0 else "warn"},
        ],
        "charts": {
            "monthly": monthly_budget_rows,
            "dimension": dimension_rows[:10],
        },
        "rows": monthly_budget_rows,
        "dimensionRows": dimension_rows[:20],
        "dimensionBaseRows": base_dimension_rows[:20],
        "insights": insights[:3],
        "alerts": alerts[:3],
    }


def build_structural_dashboards(records, current_period, previous_period, client_stats, seller_stats, family_stats, brand_stats, business_unit_stats, channel_stats, period_context, planning=None):
    filtered_records = _current_period(records, period_context)
    return {
        "history": build_history_dashboard(filtered_records, current_period, period_context, planning=planning),
        "opportunities": build_opportunities_dashboard(current_period, previous_period, client_stats, seller_stats, family_stats, channel_stats, period_context),
    }


def build_history_dashboard(records, current_period, period_context, planning=None):
    records_by_period = _group_by_period(records)
    monthly_rows = []
    for period, month_records in records_by_period.items():
        monthly_rows.append(
            {
                "label": period,
                "sales": _sum_amount(month_records),
                "quantity": _sum_quantity(month_records),
                "clients": len({_client_id(item) for item in month_records if _client_id(item)}),
                "orders": len({item.get("invoice") for item in month_records if item.get("invoice")}),
            }
        )
    sales_values = [row["sales"] for row in monthly_rows]
    quantity_values = [row["quantity"] for row in monthly_rows]
    rolling3 = _rolling_average(sales_values, 3)
    rolling6 = _rolling_average(sales_values, 6)
    for index, row in enumerate(monthly_rows):
        row["rolling3"] = rolling3[index]
        row["rolling6"] = rolling6[index]

    latest = monthly_rows[-1] if monthly_rows else {"label": "-", "sales": 0, "quantity": 0, "clients": 0}
    previous = monthly_rows[-2] if len(monthly_rows) >= 2 else {"sales": 0, "quantity": 0}
    yoy_label = None
    if latest["label"] != "-" and "-" in latest["label"]:
        year_text, month_text = latest["label"].split("-", 1)
        try:
            yoy_label = f"{int(year_text) - 1}-{month_text}"
        except ValueError:
            yoy_label = None
    yoy = next((row for row in monthly_rows if yoy_label and row["label"] == yoy_label), None)
    yoy_sales = (yoy or {}).get("sales", 0)
    yoy_units = (yoy or {}).get("quantity", 0)
    cohorts = _cohort_rows(records_by_period)
    pareto = _pareto_clients(current_period)

    current_channel_mix = _dimension_share(current_period, "channel")[:4]
    current_supplier_mix = _dimension_share(current_period, "supplier")[:4]
    current_family_mix = _dimension_share(current_period, "family")[:4]

    hhi_rows = [
        {"label": "Clientes", "value": _hhi(current_period, "client")},
        {"label": "Proveedores", "value": _hhi(current_period, "supplier")},
        {"label": "Vendedores", "value": _hhi(current_period, "seller_name")},
        {"label": "Canales", "value": _hhi(current_period, "channel")},
    ]
    top_clients_share = _safe_pct(sum(item["sales"] for item in pareto[:10]), _sum_amount(current_period))
    top_suppliers_share = _safe_pct(sum(item["sales"] for item in _dimension_share(current_period, "supplier")[:5]), _sum_amount(current_period))
    insights = [
        f"El histórico cubre {len(monthly_rows)} meses con último cierre en {latest['label']}.",
        f"La venta del último mes varía {_pct_change(latest['sales'], previous.get('sales', 0))}% vs el mes anterior y {_pct_change(latest['sales'], yoy_sales)}% interanual.",
        f"El top 10 de clientes concentra {top_clients_share}% y los 5 principales proveedores {top_suppliers_share}%.",
        f"El HHI de clientes marca {hhi_rows[0]['value']}, útil para seguir riesgo de concentración.",
    ]
    alerts = []
    if _pct_change(latest["sales"], previous.get("sales", 0)) <= -8:
        alerts.append({"severity": "warn", "text": f"El último mes cae {_pct_change(latest['sales'], previous.get('sales', 0))}% vs el mes previo."})
    if top_clients_share >= 45:
        alerts.append({"severity": "warn", "text": f"El negocio depende {top_clients_share}% de solo 10 clientes."})
    if hhi_rows[0]["value"] >= 900:
        alerts.append({"severity": "warn", "text": f"El HHI de clientes ({int(hhi_rows[0]['value'])}) sugiere concentración alta."})
    budget = _build_budget_block(monthly_rows, records_by_period, planning)

    return {
        "summary": {
            "monthsCovered": len(monthly_rows),
            "latestSales": latest["sales"],
            "latestUnits": latest["quantity"],
            "momPct": _pct_change(latest["sales"], previous.get("sales", 0)),
            "yoyPct": _pct_change(latest["sales"], yoy_sales),
            "rolling3": rolling3[-1] if rolling3 else 0,
            "rolling6": rolling6[-1] if rolling6 else 0,
            "hhiClient": hhi_rows[0]["value"],
            "top10ClientsSharePct": top_clients_share,
            "top5SuppliersSharePct": top_suppliers_share,
        },
        "kpis": [
            {"label": "Meses cubiertos", "value": len(monthly_rows), "format": "int", "sub": f"último período {latest['label']}", "tone": "neutral"},
            {"label": "MoM ventas", "value": _pct_change(latest["sales"], previous.get("sales", 0)), "format": "pct", "sub": "último mes vs anterior", "tone": "good" if _pct_change(latest['sales'], previous.get('sales', 0)) >= 0 else "warn"},
            {"label": "YoY ventas", "value": _pct_change(latest["sales"], yoy_sales), "format": "pct", "sub": "último mes vs mismo mes año anterior", "tone": "good" if _pct_change(latest['sales'], yoy_sales) >= 0 else "warn"},
            {"label": "Promedio 3M", "value": rolling3[-1] if rolling3 else 0, "format": "money", "sub": "tendencia móvil corta", "tone": "neutral"},
            {"label": "Promedio 6M", "value": rolling6[-1] if rolling6 else 0, "format": "money", "sub": "tendencia móvil estructural", "tone": "neutral"},
            {"label": "HHI clientes", "value": hhi_rows[0]["value"], "format": "int", "sub": "riesgo de concentración", "tone": "bad" if hhi_rows[0]["value"] >= 900 else "warn" if hhi_rows[0]["value"] >= 600 else "good"},
        ],
        "charts": {
            "monthly": monthly_rows,
            "cohorts": cohorts,
            "pareto": pareto[:20],
            "hhi": hhi_rows,
            "channelMix": current_channel_mix,
            "supplierMix": current_supplier_mix,
            "familyMix": current_family_mix,
        },
        "rows": monthly_rows,
        "budget": budget,
        "insights": insights[:4],
        "alerts": alerts[:4],
    }


def build_opportunities_dashboard(current_period, previous_period, client_stats, seller_stats, family_stats, channel_stats, period_context):
    client_rows = []
    channel_sales_by_client = defaultdict(list)
    for item in client_stats.values():
        if item.get("sales12m", 0) > 0:
            channel_sales_by_client[item.get("sales_force") or "Sin fuerza"].append(item.get("sales12m", 0) or 0)
    channel_benchmark = {
        channel: (sum(values) / max(len(values), 1))
        for channel, values in channel_sales_by_client.items()
    }
    for item in client_stats.values():
        if item.get("status") != "Activo":
            continue
        benchmark = channel_benchmark.get(item.get("sales_force") or "Sin fuerza", 0)
        sales = item.get("sales12m", 0) or 0
        if benchmark > 0 and sales < benchmark * 0.6:
            client_rows.append(
                {
                    "type": "Cliente bajo benchmark",
                    "name": item.get("client"),
                    "owner": item.get("seller_name") or "Sin vendedor",
                    "segment": item.get("sales_force") or "Sin fuerza de ventas",
                    "potential": round(max(benchmark - sales, 0) * 0.35, 2),
                    "detail": f"Compra {round(sales, 2)} vs benchmark {round(benchmark, 2)}",
                    "families": item.get("families", 0) or 0,
                }
            )
        if item.get("families", 0) <= 1 and sales > 0:
            client_rows.append(
                {
                    "type": "Cliente con mix corto",
                    "name": item.get("client"),
                    "owner": item.get("seller_name") or "Sin vendedor",
                    "segment": item.get("sales_force") or "Sin fuerza de ventas",
                    "potential": round(sales * 0.18, 2),
                    "detail": f"{item.get('families', 0)} familia activa",
                    "families": item.get("families", 0) or 0,
                }
            )

    seller_rows = []
    for seller_name, item in seller_stats.items():
        if seller_name == "Sin vendedor" or item.get("sales", 0) <= 0:
            continue
        avg_sales_per_client = item.get("avgSalesPerClient", 0) or 0
        if item.get("clients", 0) >= 25 and avg_sales_per_client < sum(s.get("avgSalesPerClient", 0) or 0 for s in seller_stats.values()) / max(len(seller_stats), 1) * 0.65:
            seller_rows.append(
                {
                    "type": "Vendedor con baja productividad",
                    "name": seller_name,
                    "owner": item.get("sales_force") if isinstance(item, dict) else "",
                    "segment": "Vendedor",
                    "potential": round((sum(s.get("avgSalesPerClient", 0) or 0 for s in seller_stats.values()) / max(len(seller_stats), 1) - avg_sales_per_client) * item.get("clients", 0) * 0.25, 2),
                    "detail": f"{item.get('clients', 0)} clientes con {round(avg_sales_per_client, 2)} por cliente",
                    "families": 0,
                }
            )

    channel_rows = []
    total_current_sales = _sum_amount(current_period)
    total_current_clients = len({_client_id(record) for record in current_period if _client_id(record)})
    average_sales_per_client = total_current_sales / max(total_current_clients, 1)
    for channel, item in channel_stats.items():
        if channel == "Sin canal" or item.get("clients", 0) <= 10:
            continue
        avg_sales = item.get("avgSalesPerClient", 0) or 0
        if avg_sales < average_sales_per_client * 0.75:
            channel_rows.append(
                {
                    "type": "Canal con potencial",
                    "name": channel,
                    "owner": "",
                    "segment": "Canal",
                    "potential": round((average_sales_per_client - avg_sales) * item.get("clients", 0) * 0.2, 2),
                    "detail": f"{item.get('clients', 0)} clientes con {round(avg_sales, 2)} por cliente",
                    "families": 0,
                }
            )

    all_rows = sorted(client_rows + seller_rows + channel_rows, key=lambda item: item["potential"], reverse=True)
    total_potential = round(sum(item["potential"] for item in all_rows[:30]), 2)
    insights = [
        f"Se detectan {len(client_rows)} oportunidades de cliente y {len(channel_rows)} de canal para crecimiento inmediato.",
        f"El potencial accionable priorizado suma {round(total_potential, 2)} en la cartera visible.",
        f"Los clientes con mix corto siguen siendo una palanca fuerte cuando compran 1 familia o menos.",
        f"Los canales con buen padrón pero baja venta por cliente merecen revisión de surtido y frecuencia.",
    ]
    alerts = []
    if total_potential > 0:
        alerts.append({"severity": "warn", "text": f"Hay {round(total_potential, 2)} de potencial accionable en las 30 oportunidades priorizadas."})
    if len(client_rows) >= 25:
        alerts.append({"severity": "warn", "text": f"Se acumulan {len(client_rows)} clientes por debajo del benchmark o con mix corto."})
    if len(channel_rows) >= 2:
        alerts.append({"severity": "warn", "text": f"{len(channel_rows)} canales muestran venta por cliente por debajo del promedio general."})

    return {
        "summary": {
            "opportunityCount": len(all_rows),
            "clientOpportunityCount": len(client_rows),
            "sellerOpportunityCount": len(seller_rows),
            "channelOpportunityCount": len(channel_rows),
            "totalPotential": total_potential,
        },
        "kpis": [
            {"label": "Oportunidades", "value": len(all_rows), "format": "int", "sub": f"{len(client_rows)} en clientes", "tone": "neutral"},
            {"label": "Potencial", "value": total_potential, "format": "money", "sub": "top 30 iniciativas", "tone": "good"},
            {"label": "Clientes bajo benchmark", "value": len([row for row in client_rows if row['type'] == 'Cliente bajo benchmark']), "format": "int", "sub": "gap vs referencia del segmento", "tone": "warn"},
            {"label": "Mix corto", "value": len([row for row in client_rows if row['type'] == 'Cliente con mix corto']), "format": "int", "sub": "clientes con 1 familia o menos", "tone": "warn"},
            {"label": "Canales potenciales", "value": len(channel_rows), "format": "int", "sub": "venta por cliente debajo del promedio", "tone": "warn" if channel_rows else "good"},
            {"label": "Vendedores a desarrollar", "value": len(seller_rows), "format": "int", "sub": "productividad comercial por debajo del estándar", "tone": "warn" if seller_rows else "good"},
        ],
        "charts": {
            "topPotential": all_rows[:10],
            "clients": sorted(client_rows, key=lambda item: item["potential"], reverse=True)[:8],
            "channels": sorted(channel_rows, key=lambda item: item["potential"], reverse=True)[:8],
            "sellers": sorted(seller_rows, key=lambda item: item["potential"], reverse=True)[:8],
        },
        "rows": all_rows[:80],
        "insights": insights[:4],
        "alerts": alerts[:4],
    }
