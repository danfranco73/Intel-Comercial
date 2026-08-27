from collections import defaultdict


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


def _clean_text(value):
    text = str(value or "").strip()
    return text or ""


def _first_non_empty(*values, default=""):
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return default


def _standard_key(value):
    return _clean_text(value)


def _scheme_key(value):
    return _standard_key(value)


def _looks_like_operational_route(value):
    text = _clean_text(value)
    if not text:
        return False
    compact = text.replace(" ", "")
    return compact.startswith("0000-") or compact.startswith("000-")


def _looks_like_branch_or_force_route(value, loaded):
    normalized = _normalize_text(value)
    if not normalized:
        return False
    branch_names = loaded.get("_branch_names") or set()
    force_names = loaded.get("_sales_force_names") or set()
    return normalized in branch_names or normalized in force_names


def _resolve_route_name(row, route_master=None, seller_master=None):
    commercial_route = _clean_text(row.get("commercial_route"))
    raw_route = _clean_text(row.get("route_description"))
    route_master_name = _clean_text((route_master or {}).get("route_description"))
    seller_route_name = _clean_text((seller_master or {}).get("route_description"))

    for candidate in (route_master_name, seller_route_name, commercial_route, raw_route):
        if candidate and not _looks_like_operational_route(candidate):
            return candidate

    return "Sin ruta"


def _route_rank(record):
    if not record:
        return (-1, "", "", -1)
    return (
        1 if record.get("is_active") else 0,
        record.get("valid_to") or "",
        record.get("valid_from") or "",
        record.get("client_count") or len(record.get("client_keys") or []),
    )


def _preferred_route(current, candidate):
    return candidate if _route_rank(candidate) >= _route_rank(current) else current


def sellers_with_active_scheme(seller_records, scheme_values):
    """Nombres de vendedor (normalizados) cuya fuerza de venta / esquema
    comercial vigente (maestro de vendedores) está entre los indicados. Se usa
    para excluir de un informe filtrado por esquema a vendedores cuya
    asignación actual no participa de ese esquema, aunque les haya quedado
    alguna venta histórica de otro esquema reasignada por herencia de cartera
    (ver enrich_sales_records). Se apoya en el campo del vendedor
    (sales_force/sales_scheme_name) en vez del historial de rutas por
    cliente, que puede tener asociaciones superpuestas o desactualizadas
    entre distintos esquemas para un mismo cliente."""
    markers = {_normalize_text(value) for value in (scheme_values or []) if value not in (None, "")}
    if not markers:
        return None
    eligible = set()
    for row in seller_records or []:
        if not row.get("seller_name"):
            continue
        scheme = _first_non_empty(row.get("sales_scheme_name"), row.get("sales_force"))
        if scheme and _normalize_text(scheme) in markers:
            eligible.add(_normalize_text(row["seller_name"]))
    return eligible


def build_master_maps(loaded):
    article_map = {
        row["product_key"]: row
        for row in loaded.get("articles", {}).get("records", [])
        if row.get("product_key")
    }
    route_by_seller = {}
    route_by_client = {}
    route_by_client_scheme = {}
    branch_names = set()
    sales_force_names = set()
    for row in loaded.get("routes", {}).get("records", []):
        if row.get("branch_name"):
            branch_names.add(_normalize_text(row.get("branch_name")))
        if row.get("sales_force"):
            sales_force_names.add(_normalize_text(row.get("sales_force")))
        if row.get("seller_name"):
            seller_key = _normalize_text(row["seller_name"])
            route_by_seller[seller_key] = _preferred_route(route_by_seller.get(seller_key), row)
        for client_key in row.get("client_keys") or []:
            client_key = _standard_key(client_key)
            # La cartera comparable solo puede asignarse a una ruta con vendedor.
            if client_key and row.get("seller_name"):
                route_by_client[client_key] = _preferred_route(route_by_client.get(client_key), row)
                scheme = _scheme_key(row.get("sales_scheme_key") or row.get("sales_force_key"))
                if scheme:
                    keyed_client = (client_key, scheme)
                    route_by_client_scheme[keyed_client] = _preferred_route(route_by_client_scheme.get(keyed_client), row)
    seller_by_key = {
        row["seller_key"]: row
        for row in loaded.get("sellers", {}).get("records", [])
        if row.get("seller_key")
    }
    seller_by_name = {
        _normalize_text(row["seller_name"]): row
        for row in loaded.get("sellers", {}).get("records", [])
        if row.get("seller_name")
    }
    seller_by_route = {
        _normalize_text(row["route_description"]): row
        for row in loaded.get("sellers", {}).get("records", [])
        if row.get("route_description")
    }
    return {
        "article_map": article_map,
        "route_by_seller": route_by_seller,
        "route_by_client": route_by_client,
        "route_by_client_scheme": route_by_client_scheme,
        "seller_by_key": seller_by_key,
        "seller_by_name": seller_by_name,
        "seller_by_route": seller_by_route,
        "_branch_names": branch_names,
        "_sales_force_names": sales_force_names,
    }


def enrich_sales_records(sales_records, loaded):
    master_maps = build_master_maps(loaded)
    article_map = master_maps["article_map"]
    route_by_seller = master_maps["route_by_seller"]
    route_by_client = master_maps["route_by_client"]
    route_by_client_scheme = master_maps["route_by_client_scheme"]
    seller_by_key = master_maps["seller_by_key"]
    seller_by_name = master_maps["seller_by_name"]
    seller_by_route = master_maps["seller_by_route"]

    enriched = []
    for row in sales_records:
        product_key = row.get("product_key")
        seller_key = row.get("seller_key")
        original_seller_key = seller_key
        sales_scheme_key = _scheme_key(row.get("sales_scheme_key") or row.get("sales_force_key"))
        route_description = row.get("route_description")
        seller_name_raw = row.get("seller_name")
        original_seller_name = seller_name_raw
        article = article_map.get(product_key, {})
        seller_master = seller_by_key.get(seller_key, {}) if seller_key else {}
        if not seller_master and route_description:
            seller_master = seller_by_route.get(_normalize_text(route_description), {})
        if not seller_master and seller_name_raw:
            seller_master = seller_by_name.get(_normalize_text(seller_name_raw), {})

        seller_name = _first_non_empty(seller_master.get("seller_name"), seller_name_raw, seller_key, default="Sin vendedor")
        route = route_by_seller.get(_normalize_text(seller_name), {})
        client_key = _standard_key(row.get("client_key"))
        scheme_route = route_by_client_scheme.get((client_key, sales_scheme_key), {}) if sales_scheme_key else {}
        # Si el cliente tiene una ruta vigente dentro del mismo esquema de la venta,
        # esa manda: un cliente puede tener rutas activas simultáneas en distintos
        # esquemas (ej. minorista y mayorista) y la fecha de alta de una no debe
        # pisar la asignación vigente de la otra. Solo se usa la ruta global (across
        # esquemas) cuando el cliente ya no tiene ruta activa en el esquema de la venta,
        # es decir, cuando migró de esquema por completo.
        client_route = scheme_route if scheme_route and scheme_route.get("is_active") else route_by_client.get(client_key, {})
        route = _preferred_route(route, client_route)
        if client_route and client_route.get("seller_name"):
            seller_name = _first_non_empty(client_route.get("seller_name"), seller_name)
            seller_key = _first_non_empty(client_route.get("seller_key"), seller_key)
        seller_assignment_source = "current_portfolio" if client_route and client_route.get("seller_name") else "sale_record_fallback"
        sales_force = _first_non_empty(
            row.get("sales_scheme_name"),
            row.get("sales_force"),
            route.get("sales_force"),
            seller_master.get("sales_force"),
            default="Sin fuerza de ventas",
        )
        route_name = _resolve_route_name(row, route_master=route, seller_master=seller_master)
        if _looks_like_branch_or_force_route(route_name, master_maps):
            route_name = _resolve_route_name({}, route_master=route, seller_master=seller_master)
        date_value = row["date"]
        invoice = _clean_text(row.get("invoice"))
        # La venta de referencia del análisis es el NETO sin IVA (subtotalNeto del
        # ERP), que es la base de la columna "Importe Neto" del pivot del ERP.
        # `amount` pasa a ser ese neto en todo el pipeline de análisis;
        # `amount_gross` conserva el valor con impuestos (subtotalFinal) por si
        # algún tablero necesita el "importe final".
        amount_gross = row.get("amount")
        amount_net_value = row.get("amount_net")
        if amount_net_value is None:
            amount_net_value = amount_gross
        fact_key = "|".join([
            date_value.isoformat(),
            client_key or "-",
            invoice or "-",
            _standard_key(row.get("product_key")) or "-",
            _standard_key(seller_key) or "-",
            _clean_text(route_name) or "-",
        ])
        enriched.append(
            {
                **row,
                "amount": amount_net_value,
                "amount_gross": amount_gross,
                "client": row.get("client_name") or row.get("client_key"),
                "client_canonical": _normalize_text(row.get("client_name") or row.get("client_key")),
                "period": date_value.strftime("%Y-%m"),
                "week": f"{date_value.isocalendar().year}-W{date_value.isocalendar().week:02d}",
                "seller_name": seller_name,
                "seller_key": seller_key,
                "original_seller_name": original_seller_name,
                "original_seller_key": original_seller_key,
                "seller_assignment_source": seller_assignment_source,
                "sales_scheme_key": sales_scheme_key,
                "sales_scheme_name": _first_non_empty(row.get("sales_scheme_name"), row.get("sales_force"), route.get("sales_force"), default="Sin esquema"),
                "sales_force": sales_force,
                "family": _first_non_empty(article.get("family"), default="Sin familia"),
                "line": _first_non_empty(article.get("line"), default="Sin línea"),
                "brand": _first_non_empty(article.get("brand"), default="Sin marca"),
                "business_unit": _first_non_empty(article.get("business_unit"), default="Sin unidad de negocio"),
                "segment": _first_non_empty(article.get("segment"), default="Sin segmento"),
                "division": _first_non_empty(article.get("division"), default="Sin división"),
                "supplier": _first_non_empty(article.get("supplier"), default="Sin proveedor"),
                "flavor": _first_non_empty(article.get("flavor"), default="Sin sabor"),
                "uxb": _first_non_empty(article.get("uxb"), default="Sin UxB"),
                "caliber": _first_non_empty(article.get("caliber"), default="Sin calibre"),
                "channel": _first_non_empty(row.get("channel"), default="Sin canal"),
                "route_description": route_name,
                "has_article_match": bool(article),
                "has_route_match": route_name != "Sin ruta",
                "has_seller_match": bool(seller_master or seller_key or seller_name_raw),
                "fact_key": fact_key,
            }
        )
    return enriched


def summarize_monthly_activity(records):
    monthly = defaultdict(lambda: {"clients": set(), "amount": 0.0, "quantity": 0.0, "orders": set()})
    for item in records:
        bucket = monthly[item["period"]]
        if item.get("client_key"):
            bucket["clients"].add(item["client_key"])
        if item.get("invoice"):
            bucket["orders"].add(item["invoice"])
        bucket["amount"] += item.get("amount", 0) or 0
        bucket["quantity"] += item.get("quantity", 0) or 0
    return {
        period: {
            "clients": len(values["clients"]),
            "orders": len(values["orders"]),
            "amount": round(values["amount"], 2),
            "quantity": round(values["quantity"], 2),
        }
        for period, values in sorted(monthly.items())
    }
