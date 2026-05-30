from collections import Counter


def _safe_pct(part, total):
    return round((part / max(total, 1)) * 100, 1)


def _severity_order(level):
    return {"ok": 0, "warn": 1, "critical": 2}.get(level, 0)


def evaluate_consistency(loaded, enriched_sales, selected_sales, period_context):
    scope_records = list(selected_sales or enriched_sales or [])
    total = len(scope_records)
    fact_counter = Counter(item.get("fact_key") for item in scope_records if item.get("fact_key"))
    duplicate_facts = sum(count - 1 for count in fact_counter.values() if count > 1)
    unique_clients = len({item.get("client_key") for item in scope_records if item.get("client_key")})
    unique_sellers = len({item.get("seller_name") for item in scope_records if item.get("seller_name") and item.get("seller_name") != "Sin vendedor"})
    unique_routes = len({item.get("route_description") for item in scope_records if item.get("route_description") and item.get("route_description") != "Sin ruta"})
    unique_products = len({item.get("product_key") for item in scope_records if item.get("product_key")})
    clients_per_route = round(unique_clients / max(unique_routes, 1), 2)
    sales_total = round(sum(item.get("amount", 0) or 0 for item in scope_records), 2)
    quantity_total = round(sum(item.get("quantity", 0) or 0 for item in scope_records), 2)

    route_coverage = _safe_pct(sum(1 for item in scope_records if item.get("route_description") != "Sin ruta"), total)
    seller_coverage = _safe_pct(sum(1 for item in scope_records if item.get("seller_name") != "Sin vendedor"), total)
    article_coverage = _safe_pct(sum(1 for item in scope_records if item.get("has_article_match")), total)
    channel_coverage = _safe_pct(sum(1 for item in scope_records if item.get("channel") != "Sin canal"), total)
    dated_rows = sum(1 for item in scope_records if item.get("date"))
    amount_zero_rows = sum(1 for item in scope_records if abs(item.get("amount", 0) or 0) < 0.0001)
    negative_rows = sum(1 for item in scope_records if (item.get("amount", 0) or 0) < 0)

    monthly_clients = Counter(item.get("period") for item in scope_records if item.get("client_key") and item.get("period"))
    period_labels = sorted({item.get("period") for item in scope_records if item.get("period")})

    checks = []

    def add_check(name, severity, detail, metric=None):
        checks.append({
            "name": name,
            "severity": severity,
            "detail": detail,
            "metric": metric,
        })

    duplicate_pct = _safe_pct(duplicate_facts, total)
    add_check(
        "Duplicados de hechos",
        "critical" if duplicate_pct > 2 else "warn" if duplicate_pct > 0 else "ok",
        f"{duplicate_facts} filas duplicadas sobre {total} registros del período.",
        duplicate_pct,
    )
    add_check(
        "Cobertura de rutas",
        "critical" if route_coverage < 75 else "warn" if route_coverage < 90 else "ok",
        f"{route_coverage}% de las filas tienen ruta comercial válida.",
        route_coverage,
    )
    add_check(
        "Cobertura de vendedores",
        "critical" if seller_coverage < 75 else "warn" if seller_coverage < 90 else "ok",
        f"{seller_coverage}% de las filas tienen vendedor consistente.",
        seller_coverage,
    )
    add_check(
        "Cobertura de artículos",
        "critical" if article_coverage < 75 else "warn" if article_coverage < 90 else "ok",
        f"{article_coverage}% de las filas quedaron enriquecidas con maestro de artículos.",
        article_coverage,
    )
    add_check(
        "Cobertura de canal",
        "critical" if channel_coverage < 80 else "warn" if channel_coverage < 95 else "ok",
        f"{channel_coverage}% de las filas informan canal comercial.",
        channel_coverage,
    )
    add_check(
        "Cardinalidad de rutas",
        "critical" if clients_per_route < 0.75 else "warn" if clients_per_route < 2 else "ok",
        f"Hay {unique_routes} rutas para {unique_clients} clientes únicos en el período ({clients_per_route} clientes por ruta).",
        clients_per_route,
    )
    add_check(
        "Filas con importe cero",
        "warn" if amount_zero_rows > 0 else "ok",
        f"{amount_zero_rows} filas tienen importe en cero dentro del período analizado.",
        amount_zero_rows,
    )
    add_check(
        "Filas negativas",
        "warn" if negative_rows > 0 else "ok",
        f"{negative_rows} filas muestran importe negativo y conviene revisar notas de crédito o devoluciones.",
        negative_rows,
    )
    add_check(
        "Fechas válidas",
        "ok" if dated_rows == total else "critical",
        f"{dated_rows} de {total} filas tienen fecha válida para BI.",
        _safe_pct(dated_rows, total),
    )

    overall = "ok"
    for check in checks:
        if _severity_order(check["severity"]) > _severity_order(overall):
            overall = check["severity"]

    return {
        "status": overall,
        "scope": {
            "selectedStart": period_context["selectedStart"].isoformat(),
            "selectedEnd": period_context["selectedEnd"].isoformat(),
            "comparisonStart": period_context["comparisonStart"].isoformat(),
            "comparisonEnd": period_context["comparisonEnd"].isoformat(),
        },
        "totals": {
            "rows": total,
            "sales": sales_total,
            "quantity": quantity_total,
            "uniqueClients": unique_clients,
            "uniqueSellers": unique_sellers,
            "uniqueRoutes": unique_routes,
            "uniqueProducts": unique_products,
            "clientsPerRoute": clients_per_route,
            "loadedSalesRows": loaded.get("sales", {}).get("rowsValid", total),
            "sourceCount": loaded.get("sales", {}).get("sourceCount", 1),
        },
        "coverage": {
            "routePct": route_coverage,
            "sellerPct": seller_coverage,
            "articlePct": article_coverage,
            "channelPct": channel_coverage,
        },
        "duplicates": {
            "factRows": duplicate_facts,
            "factRowsPct": duplicate_pct,
        },
        "periods": {
            "months": period_labels,
            "monthCount": len(period_labels),
            "rowsByMonth": dict(sorted(monthly_clients.items())),
        },
        "checks": checks,
    }
