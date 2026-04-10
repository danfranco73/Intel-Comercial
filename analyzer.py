from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from statistics import median

from xlsx_reader import _load_workbook, make_headers, read_sheet_rows
from schema_detector import detect_schema
from rule_engine import resolve_tasks, tasks_summary
from analysis_engine import AnalysisEngine
from kpi_generator import generate_kpis
from viz_selector import build_all_viz
from insight_writer import write_insights, insights_summary


DATASET_DEFINITIONS = {
    "sales": {
        "label": "Venta por cliente",
        "required": True,
        "fields": {
            "date": {"label": "Fecha", "required": True},
            "year": {"label": "Año", "required": False},
            "month": {"label": "Mes", "required": False},
            "client_key": {"label": "Código cliente", "required": True},
            "client_name": {"label": "Nombre cliente", "required": False},
            "route_description": {"label": "Ruta", "required": False},
            "seller_key": {"label": "Código vendedor", "required": False},
            "seller_name": {"label": "Nombre vendedor", "required": False},
            "product_key": {"label": "Código artículo", "required": False},
            "product_name": {"label": "Descripción artículo", "required": False},
            "invoice": {"label": "Comprobante/Pedido", "required": False},
            "channel": {"label": "Canal", "required": False},
            "amount": {"label": "Importe", "required": True},
            "quantity": {"label": "Cantidad", "required": False},
        },
    },
    "articles": {
        "label": "Maestro de artículos",
        "required": False,
        "fields": {
            "product_key": {"label": "Código artículo", "required": True},
            "product_name": {"label": "Descripción artículo", "required": False},
            "family": {"label": "Familia", "required": False},
            "line": {"label": "Línea", "required": False},
            "brand": {"label": "Marca", "required": False},
            "business_unit": {"label": "Unidad de negocio", "required": False},
            "segment": {"label": "Segmento", "required": False},
            "division": {"label": "División", "required": False},
            "supplier": {"label": "Proveedor", "required": False},
            "flavor": {"label": "Sabor", "required": False},
            "uxb": {"label": "UxB", "required": False},
            "caliber": {"label": "Calibre", "required": False},
        },
    },
    "routes": {
        "label": "Maestro de rutas",
        "required": False,
        "fields": {
            "sales_force": {"label": "Fuerza de ventas", "required": False},
            "seller_name": {"label": "Vendedor", "required": True},
            "route_description": {"label": "Descripción de la ruta", "required": False},
        },
    },
    "sellers": {
        "label": "Maestro de vendedores",
        "required": False,
        "fields": {
            "seller_key": {"label": "Código vendedor", "required": False},
            "seller_name": {"label": "Nombre vendedor", "required": False},
            "route_description": {"label": "Ruta", "required": False},
            "sales_force": {"label": "Fuerza de ventas", "required": False},
        },
    },
}


KEYWORDS = {
    "sales": {
        "date": ["fecha", "fec", "date", "periodo", "periodo fecha", "día", "dia"],
        "year": ["año", "ano", "year", "ejercicio"],
        "month": ["mes", "month", "periodo mes", "periodo"],
        "client_key": ["cod cliente", "codigo cliente", "id cliente", "cliente codigo", "cta", "cuenta", "nro cliente"],
        "client_name": ["cliente", "razon social", "razón social", "nombre cliente", "customer"],
        "route_description": ["ruta", "recorrido", "hoja de ruta", "descripcion ruta", "descripción ruta"],
        "seller_key": ["cod vendedor", "codigo vendedor", "id vendedor", "legajo vendedor"],
        "seller_name": ["vendedor", "asesor", "ejecutivo", "preventa", "rep"],
        "product_key": ["cod articulo", "codigo articulo", "sku", "cod producto", "id producto"],
        "product_name": ["articulo", "artículo", "producto", "descripcion articulo", "descripción artículo"],
        "invoice": ["factura", "comprobante", "pedido", "documento", "ticket"],
        "channel": ["canal", "segmento", "subcanal"],
        "amount": ["importe", "venta", "total", "neto", "monto", "facturado"],
        "quantity": ["cantidad", "unidades", "qty", "cant"],
    },
    "articles": {
        "product_key": ["cod articulo", "codigo articulo", "sku", "cod producto", "id producto", "producto estadístico", "producto estadistico"],
        "product_name": ["articulo", "artículo", "producto", "descripcion articulo", "descripción artículo", "detalle producto"],
        "family": ["familia", "rubro", "categoria", "categoría"],
        "line": ["linea", "línea"],
        "brand": ["marca"],
        "business_unit": ["unidad de negocio", "negocio"],
        "segment": ["segmento"],
        "division": ["division", "división"],
        "supplier": ["proveedor"],
        "flavor": ["sabor"],
        "uxb": ["uxb", "u x b", "unid x bulto", "unidades por bulto"],
        "caliber": ["calibre"],
    },
    "routes": {
        "sales_force": ["fuerza de ventas", "fza ventas", "fza", "fuerza"],
        "seller_name": ["vendedor", "asesor", "ejecutivo", "preventa"],
        "route_description": ["descripcion ruta", "descripción ruta", "ruta", "recorrido", "hoja de ruta"],
    },
    "sellers": {
        "seller_key": ["cod vendedor", "codigo vendedor", "id vendedor", "legajo"],
        "seller_name": ["vendedor", "asesor", "ejecutivo", "preventa", "nombre vendedor"],
        "route_description": ["ruta", "recorrido", "descripcion ruta", "descripción ruta", "hoja de ruta"],
        "sales_force": ["fuerza de ventas", "fza ventas", "fza", "fuerza"],
    },
}


FILTER_DEFINITIONS = {
    "year": {"label": "Año", "kind": "number"},
    "month": {"label": "Mes", "kind": "month"},
    "family": {"label": "Familia", "kind": "text"},
    "line": {"label": "Línea", "kind": "text"},
    "brand": {"label": "Marca", "kind": "text"},
    "business_unit": {"label": "Unidad de negocio", "kind": "text"},
    "supplier": {"label": "Proveedor", "kind": "text"},
    "sales_force": {"label": "Fuerza de ventas", "kind": "text"},
    "route_description": {"label": "Ruta", "kind": "text"},
    "seller_name": {"label": "Vendedor", "kind": "text"},
    "channel": {"label": "Canal", "kind": "text"},
}


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


def suggest_mappings(headers, dataset_type="sales"):
    dataset_type = dataset_type if dataset_type in KEYWORDS else "sales"
    suggestions = {}
    for field, keywords in KEYWORDS[dataset_type].items():
        best_idx = None
        best_score = 0
        for idx, header in enumerate(headers):
            score = score_header(header, keywords)
            if score > best_score:
                best_score = score
                best_idx = idx
        suggestions[field] = best_idx
    return suggestions


def score_header(header, keywords):
    text = normalize_text(header)
    score = 0
    for keyword in keywords:
        normalized = normalize_text(keyword)
        if normalized in text:
            score += len(normalized)
    return score


def analyze_sheet(path, sheet_name, header_row, mapping, source_name=None, filters=None):
    dataset = {
        "sales": {
            "file": source_name or getattr(path, "name", "Archivo"),
            "path": path,
            "sheet": sheet_name,
            "headerRow": header_row,
            "mapping": mapping,
        }
    }
    return analyze_datasets(dataset, filters=filters)


def analyze_sources(sources, mapping, filters=None):
    if not sources:
        raise ValueError("No hay archivos para analizar")
    datasets = {}
    for source in sources:
        datasets["sales"] = {
            "file": source["file"],
            "path": source["path"],
            "sheet": source["sheet"],
            "headerRow": source["headerRow"],
            "mapping": mapping,
        }
    return analyze_datasets(datasets, filters=filters)


def analyze_datasets(datasets, filters=None):
    if "sales" not in datasets or not datasets["sales"]:
        raise ValueError("La venta por cliente es obligatoria para analizar")

    loaded = {
        "sales": load_sales_dataset(datasets["sales"])
    }
    for dataset_type, definition in DATASET_DEFINITIONS.items():
        if dataset_type == "sales":
            continue
        source = datasets.get(dataset_type)
        if not source:
            continue
        loaded[dataset_type] = load_dataset(dataset_type, source)

    sales_records = loaded["sales"]["records"]
    article_map = {row["product_key"]: row for row in loaded.get("articles", {}).get("records", []) if row.get("product_key")}
    route_seller_map = {
        normalize_text(row["seller_name"]): row
        for row in loaded.get("routes", {}).get("records", [])
        if row.get("seller_name")
    }
    seller_key_map = {row["seller_key"]: row for row in loaded.get("sellers", {}).get("records", []) if row.get("seller_key")}
    seller_name_map = {normalize_text(row["seller_name"]): row for row in loaded.get("sellers", {}).get("records", []) if row.get("seller_name")}
    seller_route_map = {
        normalize_text(row["route_description"]): row
        for row in loaded.get("sellers", {}).get("records", [])
        if row.get("route_description")
    }

    enriched_sales = enrich_sales(sales_records, article_map, route_seller_map, seller_key_map, seller_name_map, seller_route_map)
    if not enriched_sales:
        raise ValueError("No quedaron ventas válidas para analizar")

    schema = detect_schema(enriched_sales)
    tasks  = resolve_tasks(schema)
    engine = AnalysisEngine()
    engine_results = engine.run_all(tasks, enriched_sales)
    kpi_set      = generate_kpis(engine_results, tasks)
    viz_configs  = build_all_viz(tasks, engine_results)
    dyn_insights = write_insights(kpi_set, engine_results, tasks)

    applied_filters, filtered_sales = apply_filters(enriched_sales, filters or {})
    if not filtered_sales:
        raise ValueError("No quedaron ventas para los filtros seleccionados")

    filtered_sales.sort(key=lambda item: item["date"])
    max_date = max(item["date"] for item in filtered_sales)
    current_start = max_date - timedelta(days=89)
    previous_start = current_start - timedelta(days=90)
    previous_end = current_start - timedelta(days=1)
    year_cut = max_date - timedelta(days=364)

    current_period = [item for item in filtered_sales if current_start <= item["date"] <= max_date]
    previous_period = [item for item in filtered_sales if previous_start <= item["date"] <= previous_end]
    last_12_months = [item for item in filtered_sales if item["date"] >= year_cut]

    client_stats = aggregate_clients(filtered_sales, max_date)
    seller_stats = aggregate_dimension(last_12_months, "seller_name", "seller", missing_label="Sin vendedor")
    sales_force_stats = aggregate_dimension(last_12_months, "sales_force", "sales_force", missing_label="Sin fuerza de ventas")
    family_stats = aggregate_dimension(last_12_months, "family", "family", current_start=current_start, previous_start=previous_start, previous_end=previous_end, missing_label="Sin familia")
    brand_stats = aggregate_dimension(last_12_months, "brand", "brand", current_start=current_start, previous_start=previous_start, previous_end=previous_end, missing_label="Sin marca")
    business_unit_stats = aggregate_dimension(last_12_months, "business_unit", "business_unit", current_start=current_start, previous_start=previous_start, previous_end=previous_end, missing_label="Sin unidad de negocio")
    channel_stats = aggregate_dimension(last_12_months, "channel", "channel", current_start=current_start, previous_start=previous_start, previous_end=previous_end, missing_label="Sin canal")
    route_stats = aggregate_dimension(last_12_months, "route_description", "route_description", missing_label="Sin ruta")

    coverage = build_coverage(filtered_sales)
    summary = build_summary(current_period, previous_period, client_stats, seller_stats, sales_force_stats, family_stats, brand_stats, business_unit_stats, channel_stats, route_stats, coverage)
    ratios = build_ratios(last_12_months, client_stats, seller_stats, sales_force_stats, family_stats, brand_stats, business_unit_stats, channel_stats, coverage)
    forecast = build_forecast(last_12_months)
    opportunities = build_opportunities(client_stats, sales_force_stats, family_stats, route_stats, coverage, last_12_months)
    alerts = build_alerts(summary, coverage, opportunities, seller_stats, sales_force_stats, family_stats, brand_stats, channel_stats)
    semaphores = build_semaphores(summary, coverage, forecast)
    charts = build_charts(last_12_months, sales_force_stats, seller_stats, brand_stats, channel_stats, coverage, forecast)
    rankings = build_rankings(client_stats, seller_stats, sales_force_stats, route_stats, brand_stats, business_unit_stats, channel_stats, opportunities)
    insights = build_insights(summary, coverage, forecast, opportunities, alerts)
    action_plan = build_action_plan(alerts, opportunities, forecast, coverage)
    available_filters = build_available_filters(filtered_sales)

    return {
        "meta": {
            "periodStart": min(item["date"] for item in filtered_sales).isoformat(),
            "periodEnd": max_date.isoformat(),
            "rowsUniverse": len(enriched_sales),
            "rowsAnalyzed": len(filtered_sales),
            "activeFilterSummary": summarize_filters(applied_filters),
            "datasets": build_dataset_meta(loaded),
        },
        "schema": schema,
        "availableAnalyses": tasks_summary(tasks),
        "engineResults": engine_results,
        "kpiSet": kpi_set,
        "dynamicCharts": viz_configs,
        "dynamicInsights": dyn_insights,
        "insightsSummary": insights_summary(dyn_insights),
        "summary": summary,
        "coverage": coverage,
        "ratios": ratios,
        "forecast": forecast,
        "opportunities": opportunities,
        "alerts": alerts,
        "semaphores": semaphores,
        "charts": charts,
        "rankings": rankings,
        "insights": insights,
        "actionPlan": action_plan,
        "availableFilters": available_filters,
        "appliedFilters": applied_filters,
    }


def load_dataset(dataset_type, source):
    if source.get("records") is not None:
        records = source.get("records") or []
        return {
            "datasetType": dataset_type,
            "file": source.get("file", "Fuente externa"),
            "sheet": source.get("sheet", "Datos"),
            "headerRow": int(source.get("headerRow", 0)),
            "rowsRead": int(source.get("rowsRead", len(records))),
            "rowsValid": int(source.get("rowsValid", len(records))),
            "headers": source.get("headers") or [],
            "mapping": source.get("mapping", {}),
            "records": records,
        }
    loaded = load_dataset_source(dataset_type, source, source.get("mapping", {}))
    return {
        "datasetType": dataset_type,
        "file": source["file"],
        "sheet": source["sheet"],
        "headerRow": loaded["headerRow"],
        "rowsRead": loaded["rowsRead"],
        "rowsValid": loaded["rowsValid"],
        "headers": loaded["headers"],
        "mapping": loaded["mapping"],
        "records": loaded["records"],
    }


def load_sales_dataset(config):
    if config.get("records") is not None:
        records = config.get("records") or []
        if not records:
            raise ValueError("La venta por cliente no devolvió registros válidos")
        sources = config.get("sources") or [
            {
                "file": config.get("file", "Fuente externa"),
                "sheet": config.get("sheet", "Datos"),
                "headerRow": int(config.get("headerRow", 0)),
                "rowsRead": int(config.get("rowsRead", len(records))),
                "rowsValid": int(config.get("rowsValid", len(records))),
                "sourceKind": config.get("sourceKind"),
            }
        ]
        return {
            "datasetType": "sales",
            "file": config.get("file", f"{len(sources)} fuente(s)"),
            "sheet": config.get("sheet", "Datos"),
            "headerRow": int(config.get("headerRow", 0)),
            "rowsRead": int(config.get("rowsRead", len(records))),
            "rowsValid": int(config.get("rowsValid", len(records))),
            "headers": config.get("headers") or [],
            "mapping": config.get("mapping", {}),
            "records": records,
            "sources": sources,
            "sourceCount": int(config.get("sourceCount", len(sources))),
            "sourceKind": config.get("sourceKind"),
        }

    sources = config.get("sources") or [config]
    if not sources:
        raise ValueError("La venta por cliente requiere al menos un archivo")
    mapping = config.get("mapping", {})
    merged_records = []
    source_meta = []
    total_rows = 0
    total_valid = 0
    headers = []

    for source in sources:
        loaded = load_dataset_source("sales", source, mapping)
        merged_records.extend(loaded["records"])
        total_rows += loaded["rowsRead"]
        total_valid += loaded["rowsValid"]
        if not headers:
            headers = loaded["headers"]
        source_meta.append(
            {
                "file": source["file"],
                "sheet": source["sheet"],
                "headerRow": loaded["headerRow"],
                "rowsRead": loaded["rowsRead"],
                "rowsValid": loaded["rowsValid"],
            }
        )

    if not merged_records:
        raise ValueError("La venta por cliente no dejó registros válidos después del mapeo")

    return {
        "datasetType": "sales",
        "file": f"{len(source_meta)} archivo(s)",
        "sheet": "Múltiples",
        "headerRow": source_meta[0]["headerRow"],
        "rowsRead": total_rows,
        "rowsValid": total_valid,
        "headers": headers,
        "mapping": extract_mapped_fields("sales", mapping),
        "records": merged_records,
        "sources": source_meta,
        "sourceCount": len(source_meta),
        "sourceKind": "files",
    }


def load_dataset_source(dataset_type, source, shared_mapping):
    workbook = _load_workbook(source["path"])
    rows = read_sheet_rows(workbook, source["sheet"])
    if not rows:
        raise ValueError(f"La hoja {source['sheet']} de {source['file']} no tiene filas")
    header_row = int(source.get("headerRow", 0))
    if header_row >= len(rows):
        raise ValueError(f"La fila de encabezado no existe en {source['file']}")
    mapping = extract_mapped_fields(dataset_type, shared_mapping)
    headers = make_headers(rows[header_row])
    records = normalize_rows(dataset_type, rows[header_row + 1 :], mapping, source["file"])
    if dataset_type == "sales" and not records:
        raise ValueError("La venta por cliente no dejó registros válidos después del mapeo")
    return {
        "headerRow": header_row,
        "rowsRead": len(rows),
        "rowsValid": len(records),
        "headers": headers,
        "mapping": mapping,
        "records": records,
    }


def extract_mapped_fields(dataset_type, mapping):
    definition = DATASET_DEFINITIONS[dataset_type]["fields"]
    mapped = {}
    for field in definition:
        idx = mapping.get(field)
        if idx in (None, "", -1):
            continue
        mapped[field] = int(idx)
    if dataset_type == "sales":
        if "client_key" not in mapped or "amount" not in mapped:
            raise ValueError("Venta por cliente requiere al menos Código cliente e Importe")
        if "date" not in mapped and not ({"year", "month"} <= set(mapped)):
            raise ValueError("Venta por cliente requiere Fecha o bien Año y Mes")
        missing = []
    else:
        missing = [field for field, meta in definition.items() if meta["required"] and field not in mapped]
    if missing:
        labels = ", ".join(definition[field]["label"] for field in missing)
        raise ValueError(f"Faltan campos obligatorios para {DATASET_DEFINITIONS[dataset_type]['label']}: {labels}")
    return mapped


def normalize_rows(dataset_type, rows, mapping, source_name):
    normalizers = {
        "sales": normalize_sales_row,
        "articles": normalize_article_row,
        "routes": normalize_route_row,
        "sellers": normalize_seller_row,
    }
    normalized = []
    for row_number, row in enumerate(rows, start=1):
        item = normalizers[dataset_type](row, mapping, row_number, source_name)
        if item:
            normalized.append(item)
    return normalized


def normalize_sales_row(row, mapping, row_number, source_name):
    raw_date = cell_value(row, mapping.get("date"))
    year_value = parse_year(cell_value(row, mapping.get("year")))
    month_value = parse_month(cell_value(row, mapping.get("month")))
    date_value = parse_date(raw_date) or build_period_date(year_value, month_value)
    amount = parse_number(cell_value(row, mapping.get("amount")))
    client_key = standard_key(cell_value(row, mapping.get("client_key")))
    client_name = clean_text(cell_value(row, mapping.get("client_name"))) or client_key
    if not date_value or amount is None or not client_key:
        return None
    if year_value is None:
        year_value = date_value.year
    if month_value is None:
        month_value = date_value.month
    seller_key = standard_key(cell_value(row, mapping.get("seller_key")))
    seller_name = clean_text(cell_value(row, mapping.get("seller_name")))
    route_description = clean_text(cell_value(row, mapping.get("route_description")))
    product_key = standard_key(cell_value(row, mapping.get("product_key")))
    product_name = clean_text(cell_value(row, mapping.get("product_name")))
    invoice = clean_text(cell_value(row, mapping.get("invoice"))) or invoice_fallback(client_key, date_value, row_number, source_name)
    return {
        "date": date_value,
        "year": year_value,
        "month": month_value,
        "client_key": client_key,
        "client_name": client_name,
        "route_description": route_description,
        "seller_key": seller_key,
        "seller_name": seller_name,
        "product_key": product_key,
        "product_name": product_name,
        "invoice": invoice,
        "channel": clean_text(cell_value(row, mapping.get("channel"))),
        "amount": amount,
        "quantity": parse_number(cell_value(row, mapping.get("quantity"))) or 0,
        "source": source_name,
    }


def normalize_article_row(row, mapping, row_number, source_name):
    product_key = standard_key(cell_value(row, mapping.get("product_key")))
    if not product_key:
        return None
    return {
        "product_key": product_key,
        "product_name": clean_text(cell_value(row, mapping.get("product_name"))),
        "family": clean_text(cell_value(row, mapping.get("family"))),
        "line": clean_text(cell_value(row, mapping.get("line"))),
        "brand": clean_text(cell_value(row, mapping.get("brand"))),
        "business_unit": clean_text(cell_value(row, mapping.get("business_unit"))),
        "segment": clean_text(cell_value(row, mapping.get("segment"))),
        "division": clean_text(cell_value(row, mapping.get("division"))),
        "supplier": clean_text(cell_value(row, mapping.get("supplier"))),
        "flavor": clean_text(cell_value(row, mapping.get("flavor"))),
        "uxb": clean_text(cell_value(row, mapping.get("uxb"))),
        "caliber": clean_text(cell_value(row, mapping.get("caliber"))),
    }


def normalize_route_row(row, mapping, row_number, source_name):
    seller_name = clean_text(cell_value(row, mapping.get("seller_name")))
    if not seller_name:
        return None
    return {
        "sales_force": clean_text(cell_value(row, mapping.get("sales_force"))),
        "seller_name": seller_name,
        "route_description": clean_text(cell_value(row, mapping.get("route_description"))),
    }


def normalize_seller_row(row, mapping, row_number, source_name):
    seller_key = standard_key(cell_value(row, mapping.get("seller_key")))
    seller_name = clean_text(cell_value(row, mapping.get("seller_name")))
    route_description = clean_text(cell_value(row, mapping.get("route_description")))
    sales_force = clean_text(cell_value(row, mapping.get("sales_force")))
    if not seller_key and not seller_name and not sales_force and not route_description:
        return None
    return {
        "seller_key": seller_key,
        "seller_name": seller_name or seller_key,
        "route_description": route_description,
        "sales_force": sales_force,
    }


def enrich_sales(sales_records, article_map, route_seller_map, seller_key_map, seller_name_map, seller_route_map):
    enriched = []
    for row in sales_records:
        article = article_map.get(row["product_key"], {})
        seller_master = seller_key_map.get(row["seller_key"], {}) if row["seller_key"] else {}
        if not seller_master and row["route_description"]:
            seller_master = seller_route_map.get(normalize_text(row["route_description"]), {})
        if not seller_master and row["seller_name"]:
            seller_master = seller_name_map.get(normalize_text(row["seller_name"]), {})
        seller_name = first_non_empty(row["seller_name"], seller_master.get("seller_name"), row["seller_key"], "Sin vendedor")
        route = route_seller_map.get(normalize_text(seller_name), {})
        sales_force = first_non_empty(row.get("sales_force"), route.get("sales_force"), seller_master.get("sales_force"), "Sin fuerza de ventas")
        route_name = first_non_empty(row["route_description"], route.get("route_description"), seller_master.get("route_description"), "Sin ruta")
        enriched.append(
            {
                **row,
                "client": row["client_name"] or row["client_key"],
                "seller_name": seller_name,
                "sales_force": sales_force,
                "family": first_non_empty(article.get("family"), "Sin familia"),
                "line": first_non_empty(article.get("line"), "Sin línea"),
                "brand": first_non_empty(article.get("brand"), "Sin marca"),
                "business_unit": first_non_empty(article.get("business_unit"), "Sin unidad de negocio"),
                "segment": first_non_empty(article.get("segment"), "Sin segmento"),
                "division": first_non_empty(article.get("division"), "Sin división"),
                "supplier": first_non_empty(article.get("supplier"), "Sin proveedor"),
                "flavor": first_non_empty(article.get("flavor"), "Sin sabor"),
                "uxb": first_non_empty(article.get("uxb"), "Sin UxB"),
                "caliber": first_non_empty(article.get("caliber"), "Sin calibre"),
                "channel": first_non_empty(row["channel"], "Sin canal"),
                "route_description": route_name,
                "has_article_match": bool(article),
                "has_route_match": bool(route or row.get("route_description")),
                "has_seller_match": bool(seller_master or row.get("seller_key") or row.get("seller_name")),
            }
        )
    return enriched


def aggregate_clients(records, max_date):
    clients = {}
    grouped = defaultdict(list)
    last_12_cut = max_date - timedelta(days=364)
    for item in records:
        grouped[item["client_key"]].append(item)
    for client_key, items in grouped.items():
        last_date = max(item["date"] for item in items)
        recency = (max_date - last_date).days
        dates = sorted({item["date"] for item in items})
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        avg_gap = median(gaps) if gaps else 45
        status = classify_client(recency, avg_gap)
        items_12 = [item for item in items if item["date"] >= last_12_cut]
        reference = items_12 or items
        orders = len({item["invoice"] for item in reference})
        families = {item["family"] for item in reference if item["family"] != "Sin familia"}
        clients[client_key] = {
            "client_key": client_key,
            "client": items[-1]["client"],
            "sales12m": round(sum(item["amount"] for item in items_12), 2),
            "salesHistory": round(sum(item["amount"] for item in items), 2),
            "monthsActive": len({item["date"].strftime("%Y-%m") for item in items_12}),
            "avgTicket": round(sum(item["amount"] for item in reference) / max(orders, 1), 2),
            "orders": orders,
            "families": len(families),
            "lastDate": last_date.isoformat(),
            "recencyDays": recency,
            "avgGapDays": round(avg_gap, 1),
            "status": status,
            "sales_force": most_common(items, "sales_force"),
            "route_description": most_common(items, "route_description"),
            "seller_name": most_common(items, "seller_name"),
        }
    return clients


def classify_client(recency, avg_gap):
    if recency <= max(30, avg_gap * 1.2):
        return "Activo"
    if recency <= max(60, avg_gap * 2):
        return "Dormido"
    if recency <= max(120, avg_gap * 3):
        return "Reactivable"
    return "Perdido"


def aggregate_dimension(records, key, label_key, current_start=None, previous_start=None, previous_end=None, missing_label=None):
    stats = {}
    grouped = defaultdict(list)
    for item in records:
        grouped[item.get(key) or missing_label or f"Sin {label_key}"].append(item)
    for name, items in grouped.items():
        sales = sum(item["amount"] for item in items)
        clients = len({item["client_key"] for item in items})
        orders = len(order_index(items))
        quantity = sum(item.get("quantity", 0) or 0 for item in items)
        current_sales = sum(item["amount"] for item in items if current_start and item["date"] >= current_start) if current_start else sales
        previous_sales = sum(item["amount"] for item in items if previous_start and previous_end and previous_start <= item["date"] <= previous_end) if previous_start else 0
        stats[name] = {
            label_key: name,
            "sales": round(sales, 2),
            "clients": clients,
            "orders": orders,
            "quantity": round(quantity, 2),
            "avgOrderValue": round(sales / max(orders, 1), 2),
            "avgUnitsPerOrder": round(quantity / max(orders, 1), 2),
            "avgSalesPerClient": round(sales / max(clients, 1), 2),
            "growthPct": pct_change(current_sales, previous_sales) if current_start else 0,
        }
    return stats


def build_coverage(records):
    total = max(len(records), 1)
    records_with_sales_force = sum(1 for item in records if item["sales_force"] != "Sin fuerza de ventas")
    records_with_route = sum(1 for item in records if item["route_description"] != "Sin ruta")
    records_with_article = sum(1 for item in records if item["has_article_match"])
    records_with_seller = sum(1 for item in records if item["has_seller_match"])
    sales_total = sum(item["amount"] for item in records)
    sales_without_route = sum(item["amount"] for item in records if item["route_description"] == "Sin ruta")
    sales_without_article = sum(item["amount"] for item in records if not item["has_article_match"])
    sales_without_seller = sum(item["amount"] for item in records if item["seller_name"] == "Sin vendedor")
    return {
        "routeCoveragePct": round(records_with_route / total * 100, 1),
        "salesForceCoveragePct": round(records_with_sales_force / total * 100, 1),
        "articleCoveragePct": round(records_with_article / total * 100, 1),
        "sellerCoveragePct": round(records_with_seller / total * 100, 1),
        "salesWithoutRoute": round(sales_without_route, 2),
        "salesWithoutArticle": round(sales_without_article, 2),
        "salesWithoutSeller": round(sales_without_seller, 2),
        "salesTotal": round(sales_total, 2),
    }


def build_summary(current_period, previous_period, client_stats, seller_stats, sales_force_stats, family_stats, brand_stats, business_unit_stats, channel_stats, route_stats, coverage):
    current_sales = sum(item["amount"] for item in current_period)
    previous_sales = sum(item["amount"] for item in previous_period)
    total_clients = max(len(client_stats), 1)
    current_orders = order_index(current_period)
    current_units = sum(item.get("quantity", 0) or 0 for item in current_period)
    current_order_count = len(current_orders)
    avg_order_value = round(current_sales / max(current_order_count, 1), 2)
    avg_unit_price = round(current_sales / max(current_units, 1), 2)
    avg_units_per_order = round(current_units / max(current_order_count, 1), 2)
    active = sum(1 for item in client_stats.values() if item["status"] == "Activo")
    dormant = sum(1 for item in client_stats.values() if item["status"] == "Dormido")
    reactivable = sum(1 for item in client_stats.values() if item["status"] == "Reactivable")
    lost = sum(1 for item in client_stats.values() if item["status"] == "Perdido")
    recurring = sum(1 for item in client_stats.values() if item["monthsActive"] >= 4)
    avg_ticket = round(sum(item["avgTicket"] for item in client_stats.values()) / total_clients, 2)
    avg_families = round(sum(item["families"] for item in client_stats.values()) / total_clients, 2)
    top10_share = concentration_share(client_stats, 10, "sales12m")
    seller_count = count_real_dimension_items(seller_stats, "seller")
    sales_force_count = count_real_dimension_items(sales_force_stats, "sales_force")
    route_count = count_real_dimension_items(route_stats, "route_description")
    family_count = count_real_dimension_items(family_stats, "family")
    brand_count = count_real_dimension_items(brand_stats, "brand")
    business_unit_count = count_real_dimension_items(business_unit_stats, "business_unit")
    channel_count = count_real_dimension_items(channel_stats, "channel")
    return {
        "salesCurrent": round(current_sales, 2),
        "salesPrevious": round(previous_sales, 2),
        "salesGrowthPct": pct_change(current_sales, previous_sales),
        "ordersCurrent": current_order_count,
        "unitsCurrent": round(current_units, 2),
        "avgOrderValue": avg_order_value,
        "avgUnitPrice": avg_unit_price,
        "avgUnitsPerOrder": avg_units_per_order,
        "activeClients": active,
        "activeRatioPct": round(active / total_clients * 100, 1),
        "dormantClients": dormant,
        "reactivableClients": reactivable,
        "lostClients": lost,
        "recurringClients": recurring,
        "recurringRatioPct": round(recurring / total_clients * 100, 1),
        "avgTicket": avg_ticket,
        "avgFamiliesPerClient": avg_families,
        "top10SharePct": round(top10_share, 1),
        "sellerCount": seller_count,
        "salesForceCount": sales_force_count,
        "routeCount": route_count,
        "familyCount": family_count,
        "brandCount": brand_count,
        "businessUnitCount": business_unit_count,
        "channelCount": channel_count,
        "salesPerActiveSeller": round(current_sales / max(seller_count, 1), 2),
        "topBrandSharePct": round(concentration_share(brand_stats, 1, "sales"), 1),
        "topBusinessUnitSharePct": round(concentration_share(business_unit_stats, 1, "sales"), 1),
        "topChannelSharePct": round(concentration_share(channel_stats, 1, "sales"), 1),
        "portfolioHealthPct": round((active + reactivable * 0.5) / total_clients * 100, 1),
        "routeCoveragePct": coverage["routeCoveragePct"],
        "articleCoveragePct": coverage["articleCoveragePct"],
        "sellerCoveragePct": coverage["sellerCoveragePct"],
    }


def build_ratios(records, client_stats, seller_stats, sales_force_stats, family_stats, brand_stats, business_unit_stats, channel_stats, coverage):
    total_sales = sum(item["amount"] for item in records)
    total_clients = max(len(client_stats), 1)
    total_sellers = max(count_real_dimension_items(seller_stats, "seller"), 1)
    orders = order_index(records)
    total_orders = max(len(orders), 1)
    total_units = sum(item.get("quantity", 0) or 0 for item in records)
    top_sales_force_share = concentration_share(sales_force_stats, 3, "sales")
    top_seller_share = concentration_share(seller_stats, 3, "sales")
    return {
        "salesPerClient": round(total_sales / total_clients, 2),
        "salesPerSeller": round(total_sales / total_sellers, 2),
        "clientsPerSeller": round(total_clients / total_sellers, 1),
        "ordersPerSeller": round(total_orders / total_sellers, 1),
        "avgOrderValue": round(total_sales / total_orders, 2),
        "avgUnitsPerOrder": round(total_units / total_orders, 2),
        "avgUnitPrice": round(total_sales / max(total_units, 1), 2),
        "top3SalesForcesSharePct": round(top_sales_force_share, 1),
        "top3SellersSharePct": round(top_seller_share, 1),
        "topBrandSharePct": round(concentration_share(brand_stats, 1, "sales"), 1),
        "topBusinessUnitSharePct": round(concentration_share(business_unit_stats, 1, "sales"), 1),
        "topChannelSharePct": round(concentration_share(channel_stats, 1, "sales"), 1),
        "familyBreadthPerClient": round(sum(item["families"] for item in client_stats.values()) / total_clients, 2),
        "mappedSalesPct": round(
            (total_sales - coverage["salesWithoutArticle"] - coverage["salesWithoutRoute"]) / max(total_sales, 1) * 100,
            1,
        ),
    }


def build_forecast(records):
    monthly = aggregate_monthly_sales(records)
    labels = sorted(monthly)
    values = [monthly[label] for label in labels]
    last3 = values[-3:] if len(values) >= 3 else values
    prev3 = values[-6:-3] if len(values) >= 6 else values[:-3]
    avg_last3 = sum(last3) / max(len(last3), 1)
    avg_prev3 = sum(prev3) / max(len(prev3), 1) if prev3 else avg_last3
    trend_pct = pct_change(avg_last3, avg_prev3) if avg_prev3 else 0
    moderated_trend = max(min(trend_pct, 20), -20) * 0.6
    projected_month = avg_last3 * (1 + moderated_trend / 100)
    next_labels = next_month_labels(labels[-1] if labels else date.today().strftime("%Y-%m"), 3)
    forecast_points = [{"label": label, "value": round(projected_month, 2)} for label in next_labels]
    return {
        "baseMonthlySales": round(avg_last3, 2),
        "trendPct": round(trend_pct, 1),
        "projectedQuarterSales": round(projected_month * 3, 2),
        "series": [{"label": label, "value": round(monthly[label], 2)} for label in labels],
        "forecastSeries": forecast_points,
    }


def build_opportunities(client_stats, sales_force_stats, family_stats, route_stats, coverage, records):
    dormant_value = sum(item["salesHistory"] for item in client_stats.values() if item["status"] in {"Dormido", "Reactivable"}) * 0.15
    low_breadth_clients = [item for item in client_stats.values() if item["status"] == "Activo" and item["families"] <= 1]
    avg_family_sales = sum(item["sales12m"] for item in client_stats.values()) / max(sum(max(item["families"], 1) for item in client_stats.values()), 1)
    cross_sell = len(low_breadth_clients) * avg_family_sales * 0.12
    route_gap = coverage["salesWithoutRoute"] * 0.08
    declining_routes = [item for item in route_stats.values() if item["sales"] > 0]
    top_family = max(family_stats.values(), key=lambda item: item["sales"], default={"sales": 0})
    focus_family_push = top_family["sales"] * 0.06
    total = dormant_value + cross_sell + route_gap + focus_family_push
    return {
        "recoverDormantSales": round(dormant_value, 2),
        "crossSellPotential": round(cross_sell, 2),
        "routeOptimizationPotential": round(route_gap, 2),
        "familyFocusPotential": round(focus_family_push, 2),
        "totalPotential": round(total, 2),
        "lowBreadthClients": len(low_breadth_clients),
        "routesTracked": len(declining_routes),
    }


def build_alerts(summary, coverage, opportunities, seller_stats, sales_force_stats, family_stats, brand_stats, channel_stats):
    alerts = []
    if coverage["routeCoveragePct"] < 85:
        alerts.append(alert("Datos comerciales", "Cobertura baja de rutas", f"Solo {coverage['routeCoveragePct']}% de las ventas quedó asociada a rutas.", "high"))
    if coverage["articleCoveragePct"] < 85:
        alerts.append(alert("Maestro de artículos", "Cobertura baja de artículos", f"Solo {coverage['articleCoveragePct']}% de las ventas quedó enriquecida con el maestro.", "medium"))
    if summary["top10SharePct"] >= 55:
        alerts.append(alert("Cartera", "Alta concentración en pocos clientes", f"El top 10 concentra {summary['top10SharePct']}% de la venta.", "high"))
    if opportunities["lowBreadthClients"] >= 10:
        alerts.append(alert("Surtido", "Muchos clientes con baja profundidad", f"Hay {opportunities['lowBreadthClients']} clientes activos con 1 familia o menos.", "medium"))
    top_seller = max(seller_stats.values(), key=lambda item: item["sales"], default=None)
    if top_seller and concentration_share(seller_stats, 3, "sales") >= 60:
        alerts.append(alert("Vendedores", "Dependencia comercial en pocos vendedores", f"Los 3 principales vendedores explican {round(concentration_share(seller_stats, 3, 'sales'), 1)}% de la venta.", "medium"))
    contracting = [item for item in family_stats.values() if item["growthPct"] <= -15]
    if contracting:
        family = sorted(contracting, key=lambda item: item["growthPct"])[0]
        alerts.append(alert("Mix", f"{family['family']} en retracción", f"La familia cae {family['growthPct']}% contra el trimestre previo.", "medium"))
    top_force = max(sales_force_stats.values(), key=lambda item: item["sales"], default=None)
    if top_force and concentration_share(sales_force_stats, 3, "sales") >= 60:
        alerts.append(alert("Territorio", "Concentración alta por fuerza de ventas", f"Las 3 fuerzas de ventas principales explican {round(concentration_share(sales_force_stats, 3, 'sales'), 1)}% de la venta.", "medium"))
    top_brand = max(brand_stats.values(), key=lambda item: item["sales"], default=None)
    if top_brand and concentration_share(brand_stats, 1, "sales") >= 45:
        alerts.append(alert("Mix", "Dependencia alta de una sola marca", f"La marca líder explica {round(concentration_share(brand_stats, 1, 'sales'), 1)}% de la venta.", "medium"))
    top_channel = max(channel_stats.values(), key=lambda item: item["sales"], default=None)
    if top_channel and concentration_share(channel_stats, 1, "sales") >= 55:
        alerts.append(alert("Canal", "Concentración alta por canal", f"El canal líder concentra {round(concentration_share(channel_stats, 1, 'sales'), 1)}% de la venta.", "medium"))
    return alerts[:8]


def build_semaphores(summary, coverage, forecast):
    return [
        semaphore("Crecimiento reciente", color_by_value(summary["salesGrowthPct"], [0, 8]), f"Variación 90 días: {summary['salesGrowthPct']}%"),
        semaphore("Calidad de cartera", color_by_value(summary["portfolioHealthPct"], [65, 80]), f"Cartera sana: {summary['portfolioHealthPct']}%"),
        semaphore("Cobertura de rutas", color_by_value(coverage["routeCoveragePct"], [85, 95]), f"Ventas mapeadas a rutas: {coverage['routeCoveragePct']}%"),
        semaphore("Cobertura de artículos", color_by_value(coverage["articleCoveragePct"], [85, 95]), f"Ventas enriquecidas con maestro: {coverage['articleCoveragePct']}%"),
        semaphore("Cobertura de vendedores", color_by_value(coverage["sellerCoveragePct"], [80, 95]), f"Ventas con maestro de vendedor: {coverage['sellerCoveragePct']}%"),
        semaphore("Proyección", color_by_value(forecast["trendPct"], [0, 8]), f"Proyección trimestre: {money(forecast['projectedQuarterSales'])}"),
    ]


def build_charts(records, sales_force_stats, seller_stats, brand_stats, channel_stats, coverage, forecast):
    return {
        "salesByMonth": forecast["series"],
        "salesForecast": forecast["forecastSeries"],
        "salesForceSales": top_items(sales_force_stats, "sales", "sales_force"),
        "sellerProductivity": top_items(seller_stats, "avgOrderValue", "seller"),
        "brandSales": top_items(brand_stats, "sales", "brand"),
        "channelSales": top_items(channel_stats, "sales", "channel"),
    }


def build_rankings(client_stats, seller_stats, sales_force_stats, route_stats, brand_stats, business_unit_stats, channel_stats, opportunities):
    positive_clients = sorted(client_stats.values(), key=lambda item: item["sales12m"], reverse=True)[:10]
    risk_clients = sorted(client_stats.values(), key=lambda item: (status_rank(item["status"]), -item["salesHistory"]), reverse=False)[:10]
    top_sellers = sorted(seller_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    productive_sellers = sorted(seller_stats.values(), key=lambda item: item["avgOrderValue"], reverse=True)[:10]
    top_sales_forces = sorted(sales_force_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_routes = sorted(route_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_brands = sorted(brand_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_business_units = sorted(business_unit_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_channels = sorted(channel_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    return {
        "positiveClients": positive_clients,
        "riskClients": risk_clients,
        "topSellers": top_sellers,
        "productiveSellers": productive_sellers,
        "topSalesForces": top_sales_forces,
        "topRoutes": top_routes,
        "topBrands": top_brands,
        "topBusinessUnits": top_business_units,
        "topChannels": top_channels,
        "opportunityHeadline": f"Potencial estimado capturable: {money(opportunities['totalPotential'])}",
    }


def build_insights(summary, coverage, forecast, opportunities, alerts):
    insights = [
        f"En los últimos 90 días la venta {growth_phrase(summary['salesGrowthPct'])}, con {summary['ordersCurrent']} pedidos, {int(round(summary['unitsCurrent']))} unidades y un ticket promedio de {money(summary['avgOrderValue'])}.",
        f"La productividad comercial actual equivale a {money(summary['salesPerActiveSeller'])} por vendedor activo, con un precio medio de {money(summary['avgUnitPrice'])} por unidad y {summary['avgUnitsPerOrder']} unidades por pedido.",
        f"El mix activo hoy cubre {summary['brandCount']} marcas, {summary['businessUnitCount']} unidades de negocio y {summary['channelCount']} canales. La marca líder concentra {summary['topBrandSharePct']}% y el canal principal {summary['topChannelSharePct']}%.",
        f"La calidad de datos para BI queda en rutas {coverage['routeCoveragePct']}%, artículos {coverage['articleCoveragePct']}% y vendedores {coverage['sellerCoveragePct']}% de cobertura sobre ventas.",
        f"La proyección base para el próximo trimestre es {money(forecast['projectedQuarterSales'])}, apoyada en una base mensual reciente de {money(forecast['baseMonthlySales'])} y una tendencia de {forecast['trendPct']}%.",
        f"Las palancas más claras hoy son recuperar cartera dormida ({money(opportunities['recoverDormantSales'])}), venta cruzada ({money(opportunities['crossSellPotential'])}) y optimización de ruteo ({money(opportunities['routeOptimizationPotential'])}).",
    ]
    if alerts:
        insights.append(f"La alerta principal hoy es {alerts[0]['title'].lower()}: {alerts[0]['detail']}")
    return insights[:6]


def build_action_plan(alerts, opportunities, forecast, coverage):
    actions = []
    if opportunities["recoverDormantSales"] > 0:
        actions.append(action("Plan de recuperación de cartera", "Alta", "Comercial", "15 días", f"Priorizar dormidos y reactivables con potencial de {money(opportunities['recoverDormantSales'])}."))
    if opportunities["crossSellPotential"] > 0:
        actions.append(action("Plan de profundización de surtido", "Alta", "Ventas + trade", "30 días", f"Trabajar clientes activos de baja profundidad para capturar {money(opportunities['crossSellPotential'])}."))
    if coverage["routeCoveragePct"] < 95:
        actions.append(action("Depurar maestro de rutas", "Media", "Administración comercial", "10 días", f"Hay {money(coverage['salesWithoutRoute'])} de venta sin ruta asignada."))
    if coverage["articleCoveragePct"] < 95:
        actions.append(action("Completar maestro de artículos", "Media", "Producto / sistemas", "10 días", f"Hay {money(coverage['salesWithoutArticle'])} de venta sin enriquecer con familia, marca o línea."))
    actions.append(action("Revisar proyección trimestral", "Alta", "Dirección comercial", "Próximo comité", f"Tomar la base proyectada de {money(forecast['projectedQuarterSales'])} y convertirla en meta por zona y vendedor."))
    if alerts:
        actions.append(action("Atender alerta principal", "Alta", "Gerencia comercial", "Inmediato", alerts[0]["detail"]))
    return actions[:6]


def order_index(records):
    grouped = {}
    for item in records:
        key = "|".join(
            [
                item["date"].isoformat(),
                clean_text(item.get("client_key")),
                clean_text(item.get("invoice")),
            ]
        )
        current = grouped.setdefault(
            key,
            {
                "amount": 0.0,
                "quantity": 0.0,
            },
        )
        current["amount"] += item["amount"]
        current["quantity"] += item.get("quantity", 0) or 0
    return grouped


def count_real_dimension_items(items, label_key):
    return sum(1 for item in items.values() if not is_missing_label(item.get(label_key)))


def is_missing_label(value):
    text = normalize_text(value)
    return not text or text.startswith("sin ")


def build_dataset_meta(loaded):
    info = []
    for dataset_type, dataset in loaded.items():
        extra = {}
        if dataset_type == "sales":
            extra["sourceCount"] = dataset.get("sourceCount", 1)
            extra["sources"] = dataset.get("sources", [])
            extra["sourceKind"] = dataset.get("sourceKind", "files")
        info.append(
            {
                "datasetType": dataset_type,
                "label": DATASET_DEFINITIONS[dataset_type]["label"],
                "file": dataset["file"],
                "sheet": dataset["sheet"],
                "rowsRead": dataset["rowsRead"],
                "rowsValid": dataset["rowsValid"],
                **extra,
            }
        )
    return info


def apply_filters(records, raw_filters):
    applied = {}
    for field in FILTER_DEFINITIONS:
        if field not in (raw_filters or {}):
            continue
        values = normalize_filter_values(field, raw_filters.get(field))
        if values or field in raw_filters:
            applied[field] = values
    if not applied:
        return {}, list(records)
    if any(not values for values in applied.values()):
        return applied, []

    filtered = []
    for item in records:
        if all(matches_filter(item, field, values) for field, values in applied.items()):
            filtered.append(item)
    return applied, filtered


def build_available_filters(records):
    filters = {}
    for field, meta in FILTER_DEFINITIONS.items():
        counter = Counter()
        labels = {}
        for item in records:
            value = filter_value(item, field)
            if value in (None, ""):
                continue
            counter[value] += 1
            labels[value] = filter_label(field, value)
        options = [
            {
                "value": value,
                "label": labels[value],
                "count": counter[value],
            }
            for value in sort_filter_values(field, counter.keys())
        ]
        if options:
            filters[field] = {
                "label": meta["label"],
                "kind": meta["kind"],
                "options": options,
            }
    return filters


def summarize_filters(applied_filters):
    if not applied_filters:
        return "Sin filtros aplicados"
    pieces = []
    for field, values in applied_filters.items():
        label = FILTER_DEFINITIONS[field]["label"]
        if not values:
            pieces.append(f"{label}: 0 seleccionados")
        elif len(values) == 1:
            pieces.append(f"{label}: {filter_label(field, values[0])}")
        else:
            pieces.append(f"{label}: {len(values)} seleccionados")
    return " | ".join(pieces)


def aggregate_monthly_sales(records):
    monthly = defaultdict(float)
    for item in records:
        monthly[item["date"].strftime("%Y-%m")] += item["amount"]
    return monthly


def next_month_labels(last_label, periods):
    year, month = [int(value) for value in last_label.split("-")]
    labels = []
    for _ in range(periods):
        month += 1
        if month > 12:
            month = 1
            year += 1
        labels.append(f"{year:04d}-{month:02d}")
    return labels


def top_items(items, metric, label, abs_sort=False):
    if abs_sort:
        ordered = sorted(items.values(), key=lambda item: abs(item[metric]), reverse=True)
    else:
        ordered = sorted(items.values(), key=lambda item: item[metric], reverse=True)
    return [{"label": item[label], "value": round(item[metric], 2)} for item in ordered[:8]]


def concentration_share(items, limit, metric):
    ordered = sorted((item[metric] for item in items.values()), reverse=True)
    total = sum(ordered)
    return sum(ordered[:limit]) / max(total, 1) * 100


def most_common(items, key):
    counter = Counter(item.get(key) or f"Sin {key}" for item in items)
    return counter.most_common(1)[0][0]


def status_rank(status):
    return {"Perdido": 0, "Reactivable": 1, "Dormido": 2, "Activo": 3}.get(status, 9)


def normalize_filter_values(field, values):
    if values in (None, "", []):
        return []
    if not isinstance(values, list):
        values = [values]
    normalized = []
    seen = set()
    for value in values:
        parsed = filter_value({"tmp": value}, "tmp", field_override=field)
        if parsed in (None, ""):
            continue
        marker = parsed if isinstance(parsed, (int, float)) else normalize_text(parsed)
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(parsed)
    return normalized


def matches_filter(item, field, values):
    current = filter_value(item, field)
    if current in (None, ""):
        return False
    if isinstance(current, (int, float)):
        return current in values
    return normalize_text(current) in {normalize_text(value) for value in values}


def filter_value(item, field, field_override=None):
    target = field_override or field
    value = item.get(field)
    if target == "year":
        return parse_year(value)
    if target == "month":
        return parse_month(value)
    return clean_text(value)


def filter_label(field, value):
    if field == "month":
        month_value = parse_month(value)
        return MONTH_NAMES.get(month_value, str(value))
    return str(value)


def sort_filter_values(field, values):
    values = list(values)
    if field == "year":
        return sorted(values, reverse=True)
    if field == "month":
        return sorted(values)
    return sorted(values, key=lambda value: normalize_text(value))


def cell_value(row, idx):
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = clean_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    parsed = parse_month_year_text(text)
    if parsed:
        year_value, month_value = parsed
        return build_period_date(year_value, month_value)
    return None


def parse_year(value):
    if isinstance(value, int):
        return value if 1900 <= value <= 2100 else None
    if isinstance(value, float):
        value = int(value)
        return value if 1900 <= value <= 2100 else None
    text = clean_text(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 4:
        year_value = int(digits)
        if 1900 <= year_value <= 2100:
            return year_value
    return None


def parse_month(value):
    if isinstance(value, int):
        return value if 1 <= value <= 12 else None
    if isinstance(value, float):
        value = int(value)
        return value if 1 <= value <= 12 else None
    text = normalize_text(value)
    if not text:
        return None
    if text.isdigit():
        month_value = int(text)
        return month_value if 1 <= month_value <= 12 else None
    month_aliases = {
        "ene": 1, "enero": 1, "jan": 1, "january": 1,
        "feb": 2, "febrero": 2, "february": 2,
        "mar": 3, "marzo": 3, "march": 3,
        "abr": 4, "abril": 4, "apr": 4, "april": 4,
        "may": 5, "mayo": 5,
        "jun": 6, "junio": 6, "june": 6,
        "jul": 7, "julio": 7, "july": 7,
        "ago": 8, "agosto": 8, "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "septiembre": 9, "september": 9,
        "oct": 10, "octubre": 10, "october": 10,
        "nov": 11, "noviembre": 11, "november": 11,
        "dic": 12, "diciembre": 12, "dec": 12, "december": 12,
    }
    return month_aliases.get(text)


def build_period_date(year_value, month_value):
    if year_value is None or month_value is None:
        return None
    try:
        return date(int(year_value), int(month_value), 1)
    except ValueError:
        return None


def parse_month_year_text(text):
    normalized = normalize_text(text).replace("-", " ").replace("/", " ").replace("(", " ").replace(")", " ")
    parts = [part for part in normalized.split() if part]
    found_year = None
    found_month = None
    for part in parts:
        if found_year is None:
            found_year = parse_year(part)
        if found_month is None:
            found_month = parse_month(part)
    if found_year and found_month:
        return found_year, found_month
    return None


def parse_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = clean_text(value)
    if not text:
        return None
    compact = text.replace(" ", "")
    if compact.count(",") > 0 and compact.count(".") > 0:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    else:
        compact = compact.replace(",", ".")
    try:
        return float(compact)
    except ValueError:
        return None


def standard_key(value):
    text = clean_text(value)
    return text.upper() if text else ""


def clean_text(value):
    return str(value).strip() if value not in (None, False) else ""


def normalize_text(value):
    return clean_text(value).lower()


def invoice_fallback(client_key, date_value, row_number, source_name):
    return f"{source_name}-{client_key}-{date_value.isoformat()}-{row_number}"


def first_non_empty(*values):
    for value in values:
        if clean_text(value):
            return clean_text(value)
    return ""


def pct_change(current, previous):
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round((current - previous) / previous * 100, 1)


def growth_phrase(value):
    if value > 8:
        return f"crece {value}%"
    if value < -8:
        return f"cae {value}%"
    return f"permanece estable ({value}%)"


def color_by_value(value, limits):
    if value >= limits[1]:
        return "green"
    if value >= limits[0]:
        return "yellow"
    return "red"


def semaphore(name, color, detail):
    return {"name": name, "color": color, "detail": detail}


def alert(category, title, detail, severity):
    return {"category": category, "title": title, "detail": detail, "severity": severity}


def action(title, priority, owner, horizon, detail):
    return {
        "title": title,
        "priority": priority,
        "owner": owner,
        "horizon": horizon,
        "detail": detail,
    }


def money(value):
    return f"${value:,.0f}".replace(",", ".")
