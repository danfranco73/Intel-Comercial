from __future__ import annotations

"""Vista 360 del punto de venta (PDV).

El PDV es el foco: qué compra (por empresa del grupo y por unidad de
negocio), cómo evoluciona y cómo le llegamos (fuerza de venta, ruta y
vendedor). Cada brecha se compara contra sus pares —clientes activos del
mismo tipo de negocio en la misma ventana— para que las oportunidades
tengan evidencia y un "cómo" concreto.

Consulta ClickHouse directo (sólo este cliente + agregados de pares) en vez
de correr el análisis completo del período, así la ficha responde en
segundos para cualquier ventana.
"""

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Callable

from clickhouse_client import _qualified_table, get_clickhouse_client

UNIT_MISSING = "Sin unidad de negocio"
COMPANY_MISSING = "Sin empresa"
FORCE_MISSING = "Sin fuerza de venta"

# Una unidad de negocio o empresa se considera "esperable" para el PDV cuando
# al menos esta fracción de sus pares la compra en la ventana.
EXPECTED_PENETRATION_PCT = 40.0
# Amplitud baja: compra menos de esta fracción de los SKU promedio de sus pares.
LOW_BREADTH_RATIO = 0.5
# Fuerzas a mostrar en cobertura aunque el PDV no tenga ruta ni compras.
COVERAGE_MIN_PEER_PCT = 20.0


_BAJA_ROUTE = re.compile(r"\bBAJA\b", re.IGNORECASE)


def is_covering_route(route: dict) -> bool:
    """Una ruta cubre al PDV sólo si está vigente y tiene vendedor asignado.

    El ERP usa rutas vigentes sin vendedor como depósito de clientes ("ZONA DE
    BAJA", "BAJA", la ruta "10", algunas "PYME"): nadie los visita, así que
    no cuentan como cobertura."""
    return bool(
        route.get("is_active")
        and (route.get("seller_name") or "").strip()
        and not _BAJA_ROUTE.search(route.get("route_description") or "")
    )


def _median(values) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _round(value: float, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _pct(part: float, total: float) -> float:
    return round((part / total) * 100, 1) if total else 0.0


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def _shift_year(value: date, years: int = -1) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def _month_start(value: date, months_back: int = 0) -> date:
    month_index = value.year * 12 + value.month - 1 - months_back
    return date(month_index // 12, month_index % 12 + 1, 1)


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def build_windows(end: date, window_days: int) -> dict[str, date]:
    current_start = end - timedelta(days=window_days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window_days - 1)
    return {
        "current_start": current_start,
        "current_end": end,
        "previous_start": previous_start,
        "previous_end": previous_end,
        "yoy_start": _shift_year(current_start),
        "yoy_end": _shift_year(end),
        "history_start": _month_start(end, 23),
    }


def classify_recency(recency_days: int | None) -> str:
    # Mismos umbrales base que analyzer.classify_client (sin el ajuste por
    # frecuencia, que requiere toda la cartera).
    if recency_days is None:
        return "Sin compras"
    if recency_days <= 30:
        return "Activo"
    if recency_days <= 60:
        return "Dormido"
    if recency_days <= 120:
        return "Reactivable"
    return "Perdido"


def _window_lines(lines, start: date, end: date):
    return [line for line in lines if start <= line["date"] <= end]


def _bought_products(lines, key_fn=None):
    """Productos con cantidad neta positiva (descuenta devoluciones), por grupo."""
    quantities: dict[tuple, float] = defaultdict(float)
    for line in lines:
        group = key_fn(line) if key_fn else None
        quantities[(group, line["product_key"])] += line["quantity"]
    grouped: dict[Any, set] = defaultdict(set)
    for (group, product), qty in quantities.items():
        if qty > 0:
            grouped[group].add(product)
    return grouped


def _sum_by(lines, key_fn):
    totals: dict[Any, float] = defaultdict(float)
    for line in lines:
        totals[key_fn(line)] += line["amount_net"]
    return totals


def _last_date_by(lines, key_fn):
    last: dict[Any, date] = {}
    for line in lines:
        if line["amount_net"] <= 0:
            continue
        key = key_fn(line)
        if key not in last or line["date"] > last[key]:
            last[key] = line["date"]
    return last


def _peer_stats(peer_lines, unit_of):
    """Penetración, SKU promedio y venta promedio por comprador en pares.

    `peer_lines` viene agregado por (cliente, producto, empresa, fuerza).
    """
    product_qty: dict[tuple, float] = defaultdict(float)
    client_net: dict[str, float] = defaultdict(float)
    for row in peer_lines:
        product_qty[(row["client_key"], row["product_key"])] += row["quantity"]
        client_net[row["client_key"]] += row["amount_net"]
    active_clients = {client for client, net in client_net.items() if net > 0}

    def aggregate(dimension_fn):
        buyers: dict[str, set] = defaultdict(set)
        skus: dict[tuple, set] = defaultdict(set)
        sales: dict[tuple, float] = defaultdict(float)
        for row in peer_lines:
            client = row["client_key"]
            if client not in active_clients:
                continue
            value = dimension_fn(row)
            sales[(value, client)] += row["amount_net"]
            if product_qty[(client, row["product_key"])] > 0:
                buyers[value].add(client)
                skus[(value, client)].add(row["product_key"])
        stats = {}
        total = len(active_clients)
        for value, clients in buyers.items():
            stats[value] = {
                "buyers": len(clients),
                "penetrationPct": _pct(len(clients), total),
                "avgSkus": _round(sum(len(skus[(value, c)]) for c in clients) / len(clients), 1),
                "avgSales": _round(sum(sales[(value, c)] for c in clients) / len(clients)),
                # El potencial usa la mediana: unos pocos compradores enormes
                # (p. ej. mayoristas) inflan el promedio.
                "medianSales": _round(_median(sales[(value, c)] for c in clients)),
            }
        return stats

    def channel_mix(dimension_fn, channel_fn):
        mix: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for row in peer_lines:
            if row["client_key"] in active_clients:
                mix[dimension_fn(row)][channel_fn(row)] += row["amount_net"]
        return {
            value: [name for name, amount in sorted(channels.items(), key=lambda item: -item[1]) if amount > 0]
            for value, channels in mix.items()
        }

    unit_fn = lambda row: unit_of(row["product_key"])
    company_fn = lambda row: row["company_name"] or COMPANY_MISSING
    force_fn = lambda row: row["sales_force"] or FORCE_MISSING
    return {
        "clients": len(active_clients),
        "units": aggregate(unit_fn),
        "companies": aggregate(company_fn),
        "forces": aggregate(force_fn),
        "unitForces": channel_mix(unit_fn, force_fn),
        "unitCompanies": channel_mix(unit_fn, company_fn),
        "companyForces": channel_mix(company_fn, force_fn),
    }


def _route_label(route: dict | None) -> str | None:
    if not route:
        return None
    parts = [route.get("seller_name") or "sin vendedor asignado"]
    if route.get("route_description"):
        parts.append(f"ruta {route['route_description']}")
    if route.get("visit_days"):
        parts.append(f"días de visita {route['visit_days']}")
    return " · ".join(parts)


def _how_for(route_by_force: dict[str, dict], peer_forces: list[str], client_forces: list[str] | None = None) -> dict[str, Any]:
    """Cómo llegar: la fuerza por la que sus pares compran manda; si el PDV no
    está en esa ruta se dice explícitamente y se indica por dónde le llega hoy.

    Devuelve el texto (`how`) y el canal estructurado (`channel`) para poder
    filtrar la lista de trabajo por fuerza o vendedor."""
    primary = next((force for force in peer_forces if force != FORCE_MISSING), None)

    def channel(force, on_route):
        route = route_by_force.get(force) if on_route else None
        return {
            "salesForce": force,
            "onRoute": on_route,
            "seller": (route or {}).get("seller_name") or None,
            "route": (route or {}).get("route_description") or None,
        }

    if primary and primary in route_by_force:
        return {"how": f"{primary}: {_route_label(route_by_force[primary])}", "channel": channel(primary, True)}
    fallback = next(
        (force for force in [*(client_forces or []), *peer_forces] if force in route_by_force),
        None,
    )
    if primary:
        text = f"{primary}: el PDV no está en ninguna ruta de esta fuerza (evaluar alta en ruta)"
        if fallback:
            text += f". Hoy lo atiende {fallback}: {_route_label(route_by_force[fallback])}"
        return {"how": text, "channel": channel(primary, False)}
    if fallback:
        return {"how": f"{fallback}: {_route_label(route_by_force[fallback])}", "channel": channel(fallback, True)}
    return {"how": "Sin canal identificado en los pares", "channel": None}


def _rank(opportunities: list[dict]) -> list[dict]:
    opportunities.sort(key=lambda item: -(item["potential"] or 0))
    for index, item in enumerate(opportunities, start=1):
        item["priority"] = index
        item["potential"] = _round(item["potential"])
    return opportunities


def gap_opportunities(
    *,
    units: list[dict],
    companies: list[dict],
    coverage: list[dict],
    route_by_force: dict[str, dict],
    force_peer_stats: dict[str, dict],
    peer_group: str,
    window_days: int,
) -> list[dict]:
    """Brechas del PDV frente a sus pares, cada una con su "cómo".

    Reglas compartidas por la ficha individual y la vista de cartera."""
    opportunities = []
    for item in units:
        if item["unit"] == UNIT_MISSING:
            continue
        if item["skus"] == 0 and item["peerPenetrationPct"] >= EXPECTED_PENETRATION_PCT:
            opportunities.append({
                "type": "portfolio_gap",
                "title": f"No compra {item['unit']}",
                "detail": (
                    f"Lo compra el {item['peerPenetrationPct']}% de sus pares ({peer_group}), "
                    f"con {item['peerAvgSkus']} SKU promedio."
                    + (f" Última compra: {item['lastPurchase']}." if item.get("lastPurchase") else "")
                ),
                **_how_for(route_by_force, item["peerVia"]),
                "potential": item["peerMedianSales"],
            })
        elif item["skus"] and item["peerAvgSkus"] and item["skus"] < item["peerAvgSkus"] * LOW_BREADTH_RATIO:
            opportunities.append({
                "type": "low_breadth",
                "title": f"Amplitud baja en {item['unit']}",
                "detail": f"Compra {item['skus']} SKU contra {item['peerAvgSkus']} promedio de sus pares.",
                **_how_for(route_by_force, item["peerVia"], item["via"]),
                # Estimación: la venta mediana de un par en la unidad, en
                # proporción a los SKU que le faltan.
                "potential": item["peerMedianSales"] * (1 - item["skus"] / item["peerAvgSkus"]),
            })
    # Una empresa cuyo hueco ya está explicado por una unidad de negocio que
    # ella misma abastece no se repite como oportunidad aparte.
    covered_companies = {
        (unit.get("peerCompanies") or [None])[0]
        for unit in units
        if unit["skus"] == 0 and unit["peerPenetrationPct"] >= EXPECTED_PENETRATION_PCT
    }
    for item in companies:
        if item["company"] in (COMPANY_MISSING, *covered_companies):
            continue
        if item["skus"] == 0 and item["peerPenetrationPct"] >= EXPECTED_PENETRATION_PCT:
            opportunities.append({
                "type": "company_gap",
                "title": f"No compra nada de {item['company']}",
                "detail": f"El {item['peerPenetrationPct']}% de sus pares le compra, {item['peerAvgSkus']} SKU promedio.",
                **_how_for(route_by_force, item["peerVia"]),
                "potential": item["peerMedianSales"],
            })
    gap_forces = {
        (item.get("channel") or {}).get("salesForce"): item
        for item in opportunities
        if (item.get("channel") or {}).get("onRoute") and item["type"] in {"portfolio_gap", "low_breadth"}
    }
    for item in coverage:
        if item["onRoute"] and item["sales"] <= 0:
            if item["salesForce"] in gap_forces:
                # La brecha de unidad ya apunta a esta fuerza: se suma el dato
                # de visita sin venta en vez de duplicar la oportunidad.
                gap_forces[item["salesForce"]]["detail"] += (
                    f" Está en la ruta {item['route']} de {item['salesForce']} y no compró en {window_days} días."
                )
                continue
            opportunities.append({
                "type": "visited_no_sale",
                "title": f"Visitado por {item['salesForce']} sin venta",
                "detail": (
                    f"Está en la ruta {item['route']} pero no compró en {window_days} días."
                    + (f" Última compra por esta fuerza: {item['lastPurchase']}." if item.get("lastPurchase") else "")
                ),
                **_how_for(route_by_force, [item["salesForce"]]),
                "potential": force_peer_stats.get(item["salesForce"], {}).get("medianSales", 0.0),
            })
    return opportunities


def build_pdv_360(
    *,
    client_key: str,
    client_lines: list[dict],
    peer_lines: list[dict],
    routes: list[dict],
    articles: dict[str, dict],
    windows: dict[str, date],
    last_purchase: date | None,
    first_purchase: date | None,
    profile: dict | None = None,
    company_universe: list[str] | None = None,
) -> dict[str, Any]:
    """Arma la ficha 360 a partir de datos ya cargados (sin I/O)."""
    unit_of = lambda product: (articles.get(product) or {}).get("business_unit") or UNIT_MISSING
    company_of = lambda line: line["company_name"] or COMPANY_MISSING
    force_of = lambda line: line["sales_force"] or FORCE_MISSING
    unit_line = lambda line: unit_of(line["product_key"])

    current = _window_lines(client_lines, windows["current_start"], windows["current_end"])
    previous = _window_lines(client_lines, windows["previous_start"], windows["previous_end"])
    yoy = _window_lines(client_lines, windows["yoy_start"], windows["yoy_end"])
    last_12m = _window_lines(client_lines, windows["current_end"] - timedelta(days=364), windows["current_end"])

    sales = sum(line["amount_net"] for line in current)
    previous_sales = sum(line["amount_net"] for line in previous)
    yoy_sales = sum(line["amount_net"] for line in yoy)
    orders = {line["invoice"] for line in current if line["invoice"] and line["amount_net"] > 0}
    current_skus = _bought_products(current)[None]
    previous_skus = _bought_products(previous)[None]
    recency = (windows["current_end"] - last_purchase).days if last_purchase else None

    named = sorted(client_lines, key=lambda line: line["date"], reverse=True)
    name = next((line["client_name"] for line in named if line.get("client_name")), client_key)
    business_type = next((line["business_type"] for line in named if line.get("business_type")), "")
    channel = next((line["channel"] for line in named if line.get("channel")), "")

    peers = _peer_stats(peer_lines, unit_of)

    # --- Portfolio por empresa -------------------------------------------------
    company_sales = _sum_by(current, company_of)
    company_skus = _bought_products(current, company_of)
    company_prev_skus = _bought_products(previous, company_of)
    company_last = _last_date_by(client_lines, company_of)
    company_force_sales: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    company_seller: dict[tuple, str] = {}
    for line in current:
        company_force_sales[company_of(line)][force_of(line)] += line["amount_net"]
        if line.get("seller_name"):
            company_seller[(company_of(line), force_of(line))] = line["seller_name"]
    companies_seen = set(company_universe or []) | set(company_sales) | set(peers["companies"]) | set(company_last)
    if not company_sales.get(COMPANY_MISSING):
        companies_seen.discard(COMPANY_MISSING)
    companies = []
    for company in companies_seen:
        peer = peers["companies"].get(company, {})
        skus = len(company_skus.get(company, ()))
        companies.append({
            "company": company,
            "sales": _round(company_sales.get(company, 0)),
            "sharePct": _pct(company_sales.get(company, 0), sales),
            "skus": skus,
            "previousSkus": len(company_prev_skus.get(company, ())),
            "lastPurchase": company_last[company].isoformat() if company in company_last else None,
            "status": "Compra" if skus else ("Dejó de comprar" if company in company_last else "Nunca compró"),
            "peerPenetrationPct": peer.get("penetrationPct", 0.0),
            "peerAvgSkus": peer.get("avgSkus", 0.0),
            "peerAvgSales": peer.get("avgSales", 0.0),
            "peerMedianSales": peer.get("medianSales", 0.0),
            "via": [
                {"salesForce": force, "seller": company_seller.get((company, force)), "sales": _round(amount)}
                for force, amount in sorted(company_force_sales.get(company, {}).items(), key=lambda item: -item[1])
                if amount > 0
            ],
            "peerVia": peers["companyForces"].get(company, [])[:3],
        })
    companies.sort(key=lambda item: (-item["sales"], -item["peerPenetrationPct"], item["company"]))

    # --- Portfolio por unidad de negocio ---------------------------------------
    unit_sales = _sum_by(current, unit_line)
    unit_skus = _bought_products(current, unit_line)
    unit_last = _last_date_by(client_lines, unit_line)
    unit_force_sales: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for line in current:
        unit_force_sales[unit_line(line)][force_of(line)] += line["amount_net"]
    units = []
    for unit in set(unit_sales) | set(peers["units"]):
        peer = peers["units"].get(unit, {})
        skus = len(unit_skus.get(unit, ()))
        units.append({
            "unit": unit,
            "sales": _round(unit_sales.get(unit, 0)),
            "sharePct": _pct(unit_sales.get(unit, 0), sales),
            "skus": skus,
            "lastPurchase": unit_last[unit].isoformat() if unit in unit_last else None,
            "status": "Compra" if skus else ("Dejó de comprar" if unit in unit_last else "Nunca compró"),
            "peerPenetrationPct": peer.get("penetrationPct", 0.0),
            "peerAvgSkus": peer.get("avgSkus", 0.0),
            "peerAvgSales": peer.get("avgSales", 0.0),
            "peerMedianSales": peer.get("medianSales", 0.0),
            "via": [force for force, amount in sorted(unit_force_sales.get(unit, {}).items(), key=lambda item: -item[1]) if amount > 0],
            "peerVia": peers["unitForces"].get(unit, [])[:3],
            "peerCompanies": peers["unitCompanies"].get(unit, [])[:3],
        })
    units.sort(key=lambda item: (-item["sales"], -item["peerPenetrationPct"], item["unit"]))

    # --- Cobertura: cómo le llegamos ------------------------------------------
    active_routes = [route for route in routes if is_covering_route(route)]
    route_by_force: dict[str, dict] = {}
    for route in sorted(active_routes, key=lambda item: item.get("valid_from") or ""):
        route_by_force[route.get("sales_force") or FORCE_MISSING] = route
    force_sales = _sum_by(current, force_of)
    force_skus = _bought_products(current, force_of)
    force_last = _last_date_by(client_lines, force_of)
    force_seller: dict[str, str] = {}
    for line in sorted(current, key=lambda item: item["date"]):
        if line.get("seller_name"):
            force_seller[force_of(line)] = line["seller_name"]
    forces = set(route_by_force) | {force for force, amount in force_sales.items() if amount > 0}
    forces |= {force for force, stats in peers["forces"].items() if stats["penetrationPct"] >= COVERAGE_MIN_PEER_PCT}
    forces.discard(FORCE_MISSING)
    coverage = []
    for force in forces:
        route = route_by_force.get(force)
        force_amount = force_sales.get(force, 0)
        on_route = route is not None
        if on_route and force_amount > 0:
            diagnosis, tone = "Atendido: en ruta y comprando", "good"
        elif on_route:
            diagnosis, tone = "En ruta pero sin compras en la ventana", "bad"
        elif force_amount > 0:
            diagnosis, tone = "Compra sin ruta asignada en esta fuerza", "warn"
        else:
            diagnosis, tone = "Sin cobertura: no está en ninguna ruta de esta fuerza", "warn"
        coverage.append({
            "salesForce": force,
            "onRoute": on_route,
            "route": route.get("route_description") if route else None,
            "routeSeller": route.get("seller_name") if route else None,
            "visitDays": route.get("visit_days") if route else None,
            "deliveryDays": route.get("delivery_days") if route else None,
            "mode": route.get("mode") if route else None,
            "sales": _round(force_amount),
            "skus": len(force_skus.get(force, ())),
            "lastSeller": force_seller.get(force),
            "lastPurchase": force_last[force].isoformat() if force in force_last else None,
            "peerPenetrationPct": peers["forces"].get(force, {}).get("penetrationPct", 0.0),
            "diagnosis": diagnosis,
            "tone": tone,
        })
    coverage.sort(key=lambda item: (not item["onRoute"], -item["sales"], item["salesForce"]))

    # --- Historia mensual por empresa -----------------------------------------
    history: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for line in client_lines:
        if line["date"] >= windows["history_start"]:
            history[line["date"].strftime("%Y-%m")][company_of(line)] += line["amount_net"]
    history_rows = []
    cursor = windows["history_start"]
    while cursor <= windows["current_end"]:
        period = cursor.strftime("%Y-%m")
        by_company = {key: _round(value) for key, value in history.get(period, {}).items()}
        history_rows.append({"period": period, "total": _round(sum(by_company.values())), "byCompany": by_company})
        cursor = _month_start(cursor, -1)

    # --- SKU: top, perdidos y nuevos ------------------------------------------
    def product_rows(lines):
        rows: dict[str, dict] = {}
        for line in lines:
            row = rows.setdefault(line["product_key"], {"sales": 0.0, "quantity": 0.0, "last": None, "company": None})
            row["sales"] += line["amount_net"]
            row["quantity"] += line["quantity"]
            if line["amount_net"] > 0 and (row["last"] is None or line["date"] > row["last"]):
                row["last"] = line["date"]
                row["company"] = company_of(line)
        return rows

    def describe(product, row):
        article = articles.get(product) or {}
        return {
            "productKey": product,
            "product": article.get("product_name") or product,
            "unit": unit_of(product),
            "line": article.get("line") or "",
            "brand": article.get("brand") or "",
            "company": row.get("company"),
            "sales": _round(row["sales"]),
            "quantity": _round(row["quantity"]),
            "lastPurchase": row["last"].isoformat() if row.get("last") else None,
        }

    current_products = product_rows(current)
    previous_products = product_rows(previous)
    # "Perdido" = compra habitual (en al menos 2 meses distintos de la ventana
    # anterior) que no se repitió; un SKU suelto que rota no es una pérdida.
    previous_months: dict[str, set] = defaultdict(set)
    for line in previous:
        if line["quantity"] > 0:
            previous_months[line["product_key"]].add(line["date"].strftime("%Y-%m"))
    habitual_previous = {product for product, months in previous_months.items() if len(months) >= 2}
    top = sorted(
        (describe(product, row) for product, row in current_products.items() if product in current_skus),
        key=lambda item: -item["sales"],
    )
    lost = sorted(
        (
            describe(product, row)
            for product, row in previous_products.items()
            if product in previous_skus and product in habitual_previous and product not in current_skus
        ),
        key=lambda item: -item["sales"],
    )
    new = sorted(
        (describe(product, row) for product, row in current_products.items() if product in current_skus and product not in previous_skus),
        key=lambda item: -item["sales"],
    )

    # --- Oportunidades con "cómo" ----------------------------------------------
    peer_group = business_type or "toda la cartera activa"
    opportunities = gap_opportunities(
        units=units,
        companies=companies,
        coverage=coverage,
        route_by_force=route_by_force,
        force_peer_stats=peers["forces"],
        peer_group=peer_group,
        window_days=(windows["current_end"] - windows["current_start"]).days + 1,
    )
    if lost:
        lost_forces = list(dict.fromkeys(force for item in lost[:5] for force in peers["unitForces"].get(item["unit"], [])[:1]))
        opportunities.append({
            "type": "lost_skus",
            "title": f"Dejó de comprar {len(lost)} SKU habituales",
            "detail": "Principales: " + ", ".join(item["product"] for item in lost[:3]) + ".",
            **_how_for(route_by_force, lost_forces),
            "potential": sum(item["sales"] for item in lost),
        })
    _rank(opportunities)

    monthly_avg_12m = sum(line["amount_net"] for line in last_12m) / 12
    return {
        "meta": {
            "clientKey": client_key,
            "windowStart": windows["current_start"].isoformat(),
            "windowEnd": windows["current_end"].isoformat(),
            "windowDays": (windows["current_end"] - windows["current_start"]).days + 1,
            "previousStart": windows["previous_start"].isoformat(),
            "previousEnd": windows["previous_end"].isoformat(),
            "yoyStart": windows["yoy_start"].isoformat(),
            "yoyEnd": windows["yoy_end"].isoformat(),
            "peerGroup": peer_group,
            "peerClients": peers["clients"],
            "expectedPenetrationPct": EXPECTED_PENETRATION_PCT,
        },
        "identification": {
            "clientKey": client_key,
            "name": name,
            "businessType": business_type or None,
            "channel": channel or None,
            "status": classify_recency(recency),
            "lastPurchase": last_purchase.isoformat() if last_purchase else None,
            "firstPurchase": first_purchase.isoformat() if first_purchase else None,
            "recencyDays": recency,
        },
        "finance": profile,
        "kpis": {
            "sales": _round(sales),
            "previousSales": _round(previous_sales),
            "growthPct": _pct_change(sales, previous_sales),
            "yoySales": _round(yoy_sales),
            "yoyGrowthPct": _pct_change(sales, yoy_sales),
            "monthlyAvg12m": _round(monthly_avg_12m),
            "orders": len(orders),
            "avgTicket": _round(sales / len(orders)) if orders else 0.0,
            "purchaseDays": len({line["date"] for line in current if line["amount_net"] > 0}),
            "skus": len(current_skus),
            "previousSkus": len(previous_skus),
            "companiesBought": sum(1 for item in companies if item["skus"]),
            "companiesTotal": len([item for item in companies if item["company"] != COMPANY_MISSING]),
            "unitsBought": sum(1 for item in units if item["skus"] and item["unit"] != UNIT_MISSING),
            "unitsTotal": len([item for item in units if item["unit"] != UNIT_MISSING]),
        },
        "companies": companies,
        "businessUnits": units,
        "coverage": coverage,
        "history": history_rows,
        "skus": {"top": top[:20], "lost": lost[:20], "new": new[:20]},
        "opportunities": opportunities[:8],
    }


def build_portfolio(
    *,
    lines: list[dict],
    client_meta: dict[str, dict],
    routes_by_client: dict[str, list[dict]],
    articles: dict[str, dict],
    selection: set[str],
    window_end: date,
    window_days: int,
    company_universe: list[str] | None = None,
    worklist_limit: int = 500,
    matrix_limit: int = 1500,
) -> dict[str, Any]:
    """Vista de cartera: el 360 aplicado a muchos PDV a la vez (sin I/O).

    `lines` trae la ventana de TODA la base agregada por (cliente, producto,
    empresa, fuerza): los pares de cada PDV (mismo tipo de negocio) se
    calculan sobre la base completa aunque la selección sea un vendedor."""
    unit_of = lambda product: (articles.get(product) or {}).get("business_unit") or UNIT_MISSING
    type_of = lambda client: (client_meta.get(client) or {}).get("business_type") or ""

    lines_by_client: dict[str, list[dict]] = defaultdict(list)
    lines_by_type: dict[str, list[dict]] = defaultdict(list)
    for row in lines:
        lines_by_client[row["client_key"]].append(row)
        lines_by_type[type_of(row["client_key"])].append(row)
    peers_by_type = {group: _peer_stats(rows, unit_of) for group, rows in lines_by_type.items()}
    # Sin tipo de negocio: se compara contra toda la base.
    peers_all = _peer_stats(lines, unit_of)

    units_universe = sorted(
        {unit for stats in peers_by_type.values() for unit in stats["units"]} - {UNIT_MISSING}
    )
    companies_universe = sorted(
        (set(company_universe or []) | {company for stats in peers_by_type.values() for company in stats["companies"]})
        - {COMPANY_MISSING}
    )

    matrix = []
    worklist = []
    selection_buyers_unit: dict[str, int] = defaultdict(int)
    selection_buyers_company: dict[str, int] = defaultdict(int)
    expected_unit: dict[str, float] = defaultdict(float)
    expected_company: dict[str, float] = defaultdict(float)

    for client in sorted(selection):
        rows = lines_by_client.get(client, [])
        meta = client_meta.get(client) or {}
        business_type = meta.get("business_type") or ""
        peers = peers_by_type.get(business_type) if business_type else None
        peers = peers or peers_all
        peer_group = business_type or "toda la cartera activa"

        product_qty: dict[str, float] = defaultdict(float)
        for row in rows:
            product_qty[row["product_key"]] += row["quantity"]
        bought = {product for product, qty in product_qty.items() if qty > 0}
        sales = sum(row["amount_net"] for row in rows)

        unit_skus: dict[str, set] = defaultdict(set)
        company_skus: dict[str, set] = defaultdict(set)
        unit_sales: dict[str, float] = defaultdict(float)
        company_sales: dict[str, float] = defaultdict(float)
        unit_force: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        company_force: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        force_sales: dict[str, float] = defaultdict(float)
        force_seller: dict[str, str] = {}
        for row in rows:
            unit = unit_of(row["product_key"])
            company = row["company_name"] or COMPANY_MISSING
            force = row["sales_force"] or FORCE_MISSING
            unit_sales[unit] += row["amount_net"]
            company_sales[company] += row["amount_net"]
            unit_force[unit][force] += row["amount_net"]
            company_force[company][force] += row["amount_net"]
            force_sales[force] += row["amount_net"]
            if row.get("seller_name"):
                force_seller[force] = row["seller_name"]
            if row["product_key"] in bought:
                unit_skus[unit].add(row["product_key"])
                company_skus[company].add(row["product_key"])

        ranked = lambda mapping: [key for key, value in sorted(mapping.items(), key=lambda item: -item[1]) if value > 0]
        units = []
        for unit in units_universe:
            peer = peers["units"].get(unit, {})
            units.append({
                "unit": unit,
                "skus": len(unit_skus.get(unit, ())),
                "sales": unit_sales.get(unit, 0.0),
                "peerPenetrationPct": peer.get("penetrationPct", 0.0),
                "peerAvgSkus": peer.get("avgSkus", 0.0),
                "peerAvgSales": peer.get("avgSales", 0.0),
                "peerMedianSales": peer.get("medianSales", 0.0),
                "via": ranked(unit_force.get(unit, {})),
                "peerVia": peers["unitForces"].get(unit, [])[:3],
                "peerCompanies": peers["unitCompanies"].get(unit, [])[:3],
            })
        companies = []
        for company in companies_universe:
            peer = peers["companies"].get(company, {})
            companies.append({
                "company": company,
                "skus": len(company_skus.get(company, ())),
                "sales": company_sales.get(company, 0.0),
                "peerPenetrationPct": peer.get("penetrationPct", 0.0),
                "peerAvgSkus": peer.get("avgSkus", 0.0),
                "peerAvgSales": peer.get("avgSales", 0.0),
                "peerMedianSales": peer.get("medianSales", 0.0),
                "via": ranked(company_force.get(company, {})),
                "peerVia": peers["companyForces"].get(company, [])[:3],
            })

        route_by_force: dict[str, dict] = {}
        for route in sorted((r for r in routes_by_client.get(client, []) if is_covering_route(r)), key=lambda r: r.get("valid_from") or ""):
            route_by_force[route.get("sales_force") or FORCE_MISSING] = route
        route_by_force.pop(FORCE_MISSING, None)
        coverage = [
            {
                "salesForce": force,
                "onRoute": force in route_by_force,
                "route": (route_by_force.get(force) or {}).get("route_description"),
                "sales": force_sales.get(force, 0.0),
            }
            for force in sorted(set(route_by_force) | {f for f, v in force_sales.items() if v > 0} - {FORCE_MISSING})
        ]

        last_purchase = meta.get("last_purchase")
        recency = (window_end - last_purchase).days if last_purchase else None
        if sales > 0:
            opportunities = gap_opportunities(
                units=units,
                companies=companies,
                coverage=coverage,
                route_by_force=route_by_force,
                force_peer_stats=peers["forces"],
                peer_group=peer_group,
                window_days=window_days,
            )
        else:
            # Sin compras en la ventana: la brecha es el cliente entero; las
            # brechas por unidad serían ruido.
            first_route = next(iter(route_by_force.values()), None)
            opportunities = [{
                "type": "inactive",
                "title": f"Sin compras en {window_days} días",
                "detail": (
                    f"Última compra: {last_purchase.isoformat()}." if last_purchase else "Sin compras registradas en los últimos 2 años."
                ),
                **_how_for(route_by_force, [first_route.get("sales_force")] if first_route else []),
                # Lo que este PDV compraba por mes en los últimos 12 meses,
                # llevado a la duración de la ventana.
                "potential": (meta.get("monthly_avg_12m") or 0.0) * window_days / 30,
            }]
        _rank(opportunities)

        name = meta.get("name") or client
        for item in opportunities:
            worklist.append({
                "clientKey": client,
                "client": name,
                "businessType": business_type or None,
                **item,
            })

        unit_cells = {}
        for item in units:
            if item["skus"] == 0 and item["peerPenetrationPct"] >= EXPECTED_PENETRATION_PCT:
                flag = "gap"
            elif item["skus"] and item["peerAvgSkus"] and item["skus"] < item["peerAvgSkus"] * LOW_BREADTH_RATIO:
                flag = "low"
            elif item["skus"]:
                flag = "ok"
            else:
                flag = "na"
            unit_cells[item["unit"]] = {"skus": item["skus"], "peerAvgSkus": item["peerAvgSkus"], "flag": flag}
            if item["skus"]:
                selection_buyers_unit[item["unit"]] += 1
            expected_unit[item["unit"]] += item["peerPenetrationPct"]
        company_cells = {}
        for item in companies:
            if item["skus"] == 0 and item["peerPenetrationPct"] >= EXPECTED_PENETRATION_PCT:
                flag = "gap"
            elif item["skus"]:
                flag = "ok"
            else:
                flag = "na"
            company_cells[item["company"]] = {"skus": item["skus"], "peerAvgSkus": item["peerAvgSkus"], "flag": flag}
            if item["skus"]:
                selection_buyers_company[item["company"]] += 1
            expected_company[item["company"]] += item["peerPenetrationPct"]

        matrix.append({
            "clientKey": client,
            "client": name,
            "businessType": business_type or None,
            "status": classify_recency(recency),
            "lastPurchase": last_purchase.isoformat() if last_purchase else None,
            "sales": _round(sales),
            "skus": len(bought),
            "routes": [
                {"salesForce": force, "route": route.get("route_description"), "seller": route.get("seller_name") or None}
                for force, route in sorted(route_by_force.items())
            ],
            "sellers": sorted({seller for seller in force_seller.values() if seller}),
            "companies": company_cells,
            "units": unit_cells,
            "opportunities": len(opportunities),
            "potential": _round(sum(item["potential"] for item in opportunities)),
            "topOpportunity": opportunities[0]["title"] if opportunities else None,
        })

    total = len(selection) or 1
    penetration = {
        "units": [
            {
                "unit": unit,
                "buyers": selection_buyers_unit.get(unit, 0),
                "penetrationPct": _pct(selection_buyers_unit.get(unit, 0), len(selection)),
                "expectedPct": round(expected_unit.get(unit, 0.0) / total, 1),
            }
            for unit in units_universe
        ],
        "companies": [
            {
                "company": company,
                "buyers": selection_buyers_company.get(company, 0),
                "penetrationPct": _pct(selection_buyers_company.get(company, 0), len(selection)),
                "expectedPct": round(expected_company.get(company, 0.0) / total, 1),
            }
            for company in companies_universe
        ],
    }
    worklist.sort(key=lambda item: -(item["potential"] or 0))
    type_counts: dict[str, int] = defaultdict(int)
    for item in worklist:
        type_counts[item["type"]] += 1
    matrix.sort(key=lambda item: -item["potential"])
    return {
        "meta": {
            "windowStart": (window_end - timedelta(days=window_days - 1)).isoformat(),
            "windowEnd": window_end.isoformat(),
            "windowDays": window_days,
            "expectedPenetrationPct": EXPECTED_PENETRATION_PCT,
            "units": units_universe,
            "companies": companies_universe,
        },
        "summary": {
            "clients": len(selection),
            "activeClients": sum(1 for row in matrix if row["sales"] > 0),
            "clientsWithGaps": sum(1 for row in matrix if row["opportunities"]),
            "opportunities": len(worklist),
            "potential": _round(sum(item["potential"] for item in worklist)),
            "sales": _round(sum(row["sales"] for row in matrix)),
            "byType": dict(type_counts),
        },
        "penetration": penetration,
        "matrix": matrix[:matrix_limit],
        "matrixTruncated": len(matrix) > matrix_limit,
        "worklist": worklist[:worklist_limit],
        "worklistTruncated": len(worklist) > worklist_limit,
    }


class Pdv360Service:
    def __init__(
        self,
        db,
        clickhouse_factory: Callable[[], Any] = get_clickhouse_client,
        profile_loader: Callable[[str], dict | None] | None = None,
    ):
        self.db = db
        self.clickhouse_factory = clickhouse_factory
        self.profile_loader = profile_loader

    # --- API pública ------------------------------------------------------------
    def search(self, query: str, user, data_scope, limit: int = 20) -> dict[str, Any]:
        text = str(query or "").strip()
        if len(text) < 2:
            return {"results": []}
        client = self._client()
        conditions, params = self._scope_conditions(user, data_scope)
        params["q"] = text
        where = " AND ".join(["(positionCaseInsensitiveUTF8(client_name, {q:String}) > 0 OR client_key = {q:String})", *conditions])
        rows = client.query(
            f"""
            SELECT client_key, argMax(client_name, date), max(date), argMax(business_type_name, date)
            FROM {_qualified_table()}
            WHERE {where}
            GROUP BY client_key
            ORDER BY max(date) DESC
            LIMIT {int(limit)}
            """,
            parameters=params,
        ).result_rows
        return {
            "results": [
                {"clientKey": row[0], "name": row[1], "lastPurchase": _as_date(row[2]).isoformat(), "businessType": row[3] or None}
                for row in rows
            ]
        }

    def build(self, client_key: str, fecha_hasta: str | None, window_days: int, user, data_scope) -> dict[str, Any]:
        client_key = str(client_key or "").strip()
        if not client_key:
            raise ValueError("clientKey es obligatorio")
        if not 7 <= int(window_days) <= 365:
            raise ValueError("La ventana debe estar entre 7 y 365 días")
        client = self._client()
        conditions, params = self._scope_conditions(user, data_scope, restrict_sellers=False)
        end = _as_date(fecha_hasta) if fecha_hasta else self._latest_date(client)
        windows = build_windows(end, int(window_days))
        load_start = min(windows["history_start"], windows["yoy_start"], windows["previous_start"])

        client_lines = self._client_lines(client, client_key, load_start, end, conditions, params)
        routes = list(self.db["erp_routes"].find({"client_keys": client_key}, {"_id": 0, "client_keys": 0}))
        self._authorize(client_key, client_lines, routes, user, data_scope)
        last_purchase, first_purchase = self._purchase_bounds(client, client_key, end, conditions, params)
        if last_purchase is None and not routes:
            raise LookupError("Cliente sin compras registradas ni rutas asignadas")

        business_type = next(
            (line["business_type"] for line in sorted(client_lines, key=lambda l: l["date"], reverse=True) if line["business_type"]),
            "",
        )
        peer_lines = self._peer_lines(client, client_key, business_type, windows, conditions, params)
        articles = {
            row["product_key"]: row
            for row in self.db["erp_articles"].find(
                {}, {"_id": 0, "product_key": 1, "product_name": 1, "business_unit": 1, "line": 1, "brand": 1, "family": 1}
            )
            if row.get("product_key")
        }
        result = build_pdv_360(
            client_key=client_key,
            client_lines=client_lines,
            peer_lines=peer_lines,
            routes=routes,
            articles=articles,
            windows=windows,
            last_purchase=last_purchase,
            first_purchase=first_purchase,
            profile=self._profile(client_key),
            company_universe=self._company_universe(client),
        )
        result["meta"]["scopeRestricted"] = bool(conditions)
        return result

    def portfolio_options(self, user, data_scope) -> dict[str, Any]:
        routes = self._active_routes()
        allowed = self._allowed_seller_names(data_scope)
        sellers: dict[str, set] = defaultdict(set)
        route_options = []
        for route in routes:
            seller = route.get("seller_name") or ""
            if allowed is not None and seller not in allowed:
                continue
            if seller:
                sellers[seller].add(route.get("sales_force") or "")
            route_options.append({
                "id": self._route_id(route),
                "label": f"{route.get('sales_force') or '-'} · {route.get('route_description') or route.get('route_key')}"
                + (f" · {seller}" if seller else ""),
                "salesForce": route.get("sales_force"),
                "seller": seller or None,
                "clients": len(route.get("client_keys") or []),
            })
        client = self._client()
        types = client.query(
            f"""
            SELECT business_type_name, uniqExact(client_key) FROM {_qualified_table()}
            WHERE date >= today() - 180 AND business_type_name != ''
            GROUP BY business_type_name ORDER BY 2 DESC
            """
        ).result_rows
        return {
            "sellers": [
                {"name": name, "salesForces": sorted(force for force in forces if force)}
                for name, forces in sorted(sellers.items())
            ],
            "routes": sorted(route_options, key=lambda item: item["label"]),
            "salesForces": sorted({route.get("sales_force") for route in routes if route.get("sales_force")}),
            "businessTypes": [{"name": row[0], "clients": row[1]} for row in types],
            "restrictedToSellers": allowed is not None,
        }

    def portfolio(self, filters: dict[str, str], fecha_hasta: str | None, window_days: int, user, data_scope) -> dict[str, Any]:
        if not 7 <= int(window_days) <= 365:
            raise ValueError("La ventana debe estar entre 7 y 365 días")
        client = self._client()
        conditions, params = self._scope_conditions(user, data_scope, restrict_sellers=False)
        end = _as_date(fecha_hasta) if fecha_hasta else self._latest_date(client)
        start = end - timedelta(days=int(window_days) - 1)
        lines = self._base_lines(client, start, end, conditions, params)
        client_meta = self._client_meta(client, end)
        routes = self._active_routes()
        routes_by_client: dict[str, list[dict]] = defaultdict(list)
        for route in routes:
            for key in route.get("client_keys") or []:
                routes_by_client[key].append(route)

        selection = self._select_clients(filters, lines, routes_by_client, client_meta, data_scope)
        articles = {
            row["product_key"]: row
            for row in self.db["erp_articles"].find({}, {"_id": 0, "product_key": 1, "business_unit": 1})
            if row.get("product_key")
        }
        result = build_portfolio(
            lines=lines,
            client_meta=client_meta,
            routes_by_client=routes_by_client,
            articles=articles,
            selection=selection,
            window_end=end,
            window_days=int(window_days),
            company_universe=self._company_universe(client),
        )
        result["meta"]["filters"] = {key: value for key, value in filters.items() if value}
        result["meta"]["scopeRestricted"] = bool(conditions) or "seller_name" in (data_scope or {})
        return result

    def _select_clients(self, filters, lines, routes_by_client, client_meta, data_scope) -> set[str]:
        seller = (filters.get("seller") or "").strip()
        route_id = (filters.get("route") or "").strip()
        force = (filters.get("salesForce") or "").strip()
        business_type = (filters.get("businessType") or "").strip()
        allowed = self._allowed_seller_names(data_scope)

        sale_sellers: dict[str, set] = defaultdict(set)
        sale_forces: dict[str, set] = defaultdict(set)
        active_sales: dict[str, float] = defaultdict(float)
        for row in lines:
            active_sales[row["client_key"]] += row["amount_net"]
            if row.get("seller_name"):
                sale_sellers[row["client_key"]].add(row["seller_name"])
            if row.get("sales_force"):
                sale_forces[row["client_key"]].add(row["sales_force"])

        universe = {client for client, amount in active_sales.items() if amount > 0}
        universe |= {client for client, routes in routes_by_client.items() if routes}

        def route_sellers(client):
            return {route.get("seller_name") for route in routes_by_client.get(client, []) if route.get("seller_name")}

        selected = set()
        for client in universe:
            sellers = route_sellers(client) | sale_sellers.get(client, set())
            if allowed is not None and not sellers & allowed:
                continue
            if seller and seller not in sellers:
                continue
            if route_id and not any(self._route_id(route) == route_id for route in routes_by_client.get(client, [])):
                continue
            if force and force not in (
                {route.get("sales_force") for route in routes_by_client.get(client, [])} | sale_forces.get(client, set())
            ):
                continue
            if business_type and (client_meta.get(client) or {}).get("business_type") != business_type:
                continue
            selected.add(client)
        return selected

    # --- Acceso a datos ---------------------------------------------------------
    def _client(self):
        client = self.clickhouse_factory()
        if client is None:
            raise RuntimeError("ClickHouse no está configurado")
        return client

    def _latest_date(self, client) -> date:
        value = client.query(f"SELECT max(date) FROM {_qualified_table()}").result_rows[0][0]
        if not value:
            raise LookupError("No hay ventas cargadas")
        return _as_date(value)

    def _client_lines(self, client, client_key, start, end, conditions, params):
        where = " AND ".join(["client_key = {client_key:String}", "date >= {start:Date}", "date <= {end:Date}", *conditions])
        rows = client.query(
            f"""
            SELECT date, product_key, invoice, company_name, sales_scheme_name, seller_name,
                   business_type_name, channel, client_name, amount_net, quantity
            FROM {_qualified_table()}
            WHERE {where}
            """,
            parameters={**params, "client_key": client_key, "start": start, "end": end},
        ).result_rows
        return [
            {
                "date": _as_date(row[0]),
                "product_key": row[1],
                "invoice": row[2],
                "company_name": row[3],
                "sales_force": row[4],
                "seller_name": row[5],
                "business_type": row[6],
                "channel": row[7],
                "client_name": row[8],
                "amount_net": float(row[9] or 0),
                "quantity": float(row[10] or 0),
            }
            for row in rows
        ]

    def _purchase_bounds(self, client, client_key, end, conditions, params):
        where = " AND ".join(["client_key = {client_key:String}", "date <= {end:Date}", "amount_net > 0", *conditions])
        row = client.query(
            f"SELECT max(date), min(date), count() FROM {_qualified_table()} WHERE {where}",
            parameters={**params, "client_key": client_key, "end": end},
        ).result_rows[0]
        if not row[2]:
            return None, None
        return _as_date(row[0]), _as_date(row[1])

    def _peer_lines(self, client, client_key, business_type, windows, conditions, params):
        base = ["date >= {start:Date}", "date <= {end:Date}", "client_key != {client_key:String}", *conditions]
        peer_filter = ""
        if business_type:
            peer_filter = f"""
              AND client_key IN (
                SELECT client_key FROM {_qualified_table()}
                WHERE date >= {{start:Date}} AND date <= {{end:Date}}
                GROUP BY client_key
                HAVING argMax(business_type_name, date) = {{business_type:String}}
              )"""
        rows = client.query(
            f"""
            SELECT client_key, product_key, company_name, sales_scheme_name,
                   sum(amount_net), sum(quantity)
            FROM {_qualified_table()}
            WHERE {" AND ".join(base)} {peer_filter}
            GROUP BY client_key, product_key, company_name, sales_scheme_name
            """,
            parameters={
                **params,
                "client_key": client_key,
                "business_type": business_type,
                "start": windows["current_start"],
                "end": windows["current_end"],
            },
        ).result_rows
        return [
            {
                "client_key": row[0],
                "product_key": row[1],
                "company_name": row[2],
                "sales_force": row[3],
                "amount_net": float(row[4] or 0),
                "quantity": float(row[5] or 0),
            }
            for row in rows
        ]

    def _base_lines(self, client, start, end, conditions, params):
        where = " AND ".join(["date >= {start:Date}", "date <= {end:Date}", *conditions])
        rows = client.query(
            f"""
            SELECT client_key, product_key, company_name, sales_scheme_name,
                   sum(amount_net), sum(quantity), argMax(seller_name, date)
            FROM {_qualified_table()}
            WHERE {where}
            GROUP BY client_key, product_key, company_name, sales_scheme_name
            """,
            parameters={**params, "start": start, "end": end},
        ).result_rows
        return [
            {
                "client_key": row[0],
                "product_key": row[1],
                "company_name": row[2],
                "sales_force": row[3],
                "amount_net": float(row[4] or 0),
                "quantity": float(row[5] or 0),
                "seller_name": row[6],
            }
            for row in rows
        ]

    def _client_meta(self, client, end) -> dict[str, dict]:
        rows = client.query(
            f"""
            SELECT client_key, argMax(client_name, date),
                   argMaxIf(business_type_name, date, business_type_name != ''), max(date),
                   sumIf(amount_net, date > {{year_ago:Date}}) / 12
            FROM {_qualified_table()}
            WHERE date >= {{start:Date}} AND date <= {{end:Date}} AND amount_net > 0
            GROUP BY client_key
            """,
            parameters={"start": _shift_year(end, -2), "end": end, "year_ago": _shift_year(end, -1)},
        ).result_rows
        return {
            row[0]: {
                "name": row[1],
                "business_type": row[2] or "",
                "last_purchase": _as_date(row[3]),
                "monthly_avg_12m": float(row[4] or 0),
            }
            for row in rows
        }

    def _active_routes(self) -> list[dict]:
        return [route for route in self.db["erp_routes"].find({"is_active": True}, {"_id": 0}) if is_covering_route(route)]

    @staticmethod
    def _route_id(route: dict) -> str:
        return f"{route.get('sales_force_key') or route.get('sales_force') or ''}|{route.get('route_key') or ''}"

    def _allowed_seller_names(self, data_scope) -> set[str] | None:
        scope = data_scope or {}
        if "seller_name" not in scope:
            return None
        return set(scope["seller_name"] or [])

    def _company_universe(self, client) -> list[str]:
        rows = client.query(
            f"""
            SELECT DISTINCT company_name FROM {_qualified_table()}
            WHERE date >= today() - 365 AND company_name != ''
            """
        ).result_rows
        return sorted(row[0] for row in rows)

    def _profile(self, client_key: str) -> dict | None:
        loader = self.profile_loader
        if loader is None:
            try:
                from supabase_client import fetch_client_profile, supabase_configured
            except ImportError:
                return None
            if not supabase_configured():
                return None
            loader = fetch_client_profile
        try:
            return loader(client_key)
        except Exception:
            # La ficha no depende de Supabase: sin perfil financiero se muestra igual.
            return None

    # --- Alcance ----------------------------------------------------------------
    def _scope_conditions(self, user, data_scope, restrict_sellers=True):
        """Filtros a nivel de línea de venta según el alcance del usuario.

        La restricción por vendedor se valida aparte (acceso al cliente); una
        vez autorizado el cliente se muestra todo lo que compra, salvo las
        restricciones por fuerza de venta y por artículo.
        """
        scope = data_scope or {}
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if scope.get("sales_force"):
            conditions.append("sales_scheme_name IN {scope_forces:Array(String)}")
            params["scope_forces"] = list(scope["sales_force"])
        article_filters = {
            field: set(scope[field])
            for field in ("business_unit", "line", "supplier")
            if scope.get(field)
        }
        if article_filters:
            allowed = [
                row["product_key"]
                for row in self.db["erp_articles"].find({}, {"_id": 0, "product_key": 1, "business_unit": 1, "line": 1, "supplier": 1})
                if row.get("product_key") and all(row.get(field) in values for field, values in article_filters.items())
            ]
            conditions.append("product_key IN {scope_products:Array(String)}")
            params["scope_products"] = allowed
        if restrict_sellers and "seller_name" in scope:
            conditions.append("seller_key IN {scope_sellers:Array(String)}")
            params["scope_sellers"] = self._seller_keys(scope["seller_name"])
        return conditions, params

    def _seller_keys(self, seller_names) -> list[str]:
        return sorted(
            {
                str(row.get("seller_key"))
                for row in self.db["erp_sellers"].find({"seller_name": {"$in": list(seller_names or [])}}, {"_id": 0, "seller_key": 1})
                if row.get("seller_key")
            }
        )

    def _authorize(self, client_key, client_lines, routes, user, data_scope):
        scope = data_scope or {}
        if "seller_name" not in scope:
            return
        allowed_names = set(scope["seller_name"] or [])
        if any(line.get("seller_name") in allowed_names for line in client_lines):
            return
        if any(is_covering_route(route) and route.get("seller_name") in allowed_names for route in routes):
            return
        raise PermissionError("El cliente está fuera de tu cartera")
