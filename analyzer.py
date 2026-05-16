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
    "product_name": {"label": "Producto", "kind": "text"},
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


def analyze_datasets(datasets, filters=None, supplier_focus=None):
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

    sales_context = resolve_period_context(loaded["sales"], enriched_sales)
    selected_unfiltered_sales = records_between(
        enriched_sales,
        sales_context["selectedStart"],
        sales_context["selectedEnd"],
    )

    normalized_filters = remove_noop_filters(filters or {}, selected_unfiltered_sales, sales_context)
    base_filters = remove_filter_field(normalized_filters, "supplier")
    _, supplier_context_sales = apply_filters(enriched_sales, base_filters)
    if not supplier_context_sales:
        base_filters = relax_broad_filters(base_filters, selected_unfiltered_sales, sales_context)
        _, supplier_context_sales = apply_filters(enriched_sales, base_filters)
        if not supplier_context_sales:
            raise ValueError("No quedaron ventas para los filtros seleccionados")

    effective_filters = merge_supplier_focus_filter(normalized_filters, supplier_focus)
    applied_filters, filtered_sales = apply_filters(enriched_sales, effective_filters)
    if not filtered_sales:
        effective_filters = relax_broad_filters(effective_filters, selected_unfiltered_sales, sales_context)
        applied_filters, filtered_sales = apply_filters(enriched_sales, effective_filters)
        if not filtered_sales:
            raise ValueError("No quedaron ventas para los filtros seleccionados")

    filtered_sales.sort(key=lambda item: item["date"])
    supplier_context_sales.sort(key=lambda item: item["date"])
    current_period = records_between(filtered_sales, sales_context["selectedStart"], sales_context["selectedEnd"])
    if not current_period:
        raise ValueError("No quedaron ventas dentro del período seleccionado para los filtros aplicados")
    previous_period = records_between(filtered_sales, sales_context["comparisonStart"], sales_context["comparisonEnd"])
    supplier_context_current_period = records_between(
        supplier_context_sales,
        sales_context["selectedStart"],
        sales_context["selectedEnd"],
    )
    supplier_context_previous_period = records_between(
        supplier_context_sales,
        sales_context["comparisonStart"],
        sales_context["comparisonEnd"],
    )

    max_date = sales_context["selectedEnd"]
    client_stats = aggregate_clients(
        filtered_sales,
        sales_context["selectedStart"],
        sales_context["selectedEnd"],
        sales_context["comparisonStart"],
        sales_context["comparisonEnd"],
    )
    seller_stats = aggregate_dimension(
        filtered_sales,
        "seller_name",
        "seller",
        current_start=sales_context["selectedStart"],
        current_end=sales_context["selectedEnd"],
        previous_start=sales_context["comparisonStart"],
        previous_end=sales_context["comparisonEnd"],
        missing_label="Sin vendedor",
    )
    sales_force_stats = aggregate_dimension(
        filtered_sales,
        "sales_force",
        "sales_force",
        current_start=sales_context["selectedStart"],
        current_end=sales_context["selectedEnd"],
        previous_start=sales_context["comparisonStart"],
        previous_end=sales_context["comparisonEnd"],
        missing_label="Sin fuerza de ventas",
    )
    family_stats = aggregate_dimension(
        filtered_sales,
        "family",
        "family",
        current_start=sales_context["selectedStart"],
        current_end=sales_context["selectedEnd"],
        previous_start=sales_context["comparisonStart"],
        previous_end=sales_context["comparisonEnd"],
        missing_label="Sin familia",
    )
    brand_stats = aggregate_dimension(
        filtered_sales,
        "brand",
        "brand",
        current_start=sales_context["selectedStart"],
        current_end=sales_context["selectedEnd"],
        previous_start=sales_context["comparisonStart"],
        previous_end=sales_context["comparisonEnd"],
        missing_label="Sin marca",
    )
    business_unit_stats = aggregate_dimension(
        filtered_sales,
        "business_unit",
        "business_unit",
        current_start=sales_context["selectedStart"],
        current_end=sales_context["selectedEnd"],
        previous_start=sales_context["comparisonStart"],
        previous_end=sales_context["comparisonEnd"],
        missing_label="Sin unidad de negocio",
    )
    channel_stats = aggregate_dimension(
        filtered_sales,
        "channel",
        "channel",
        current_start=sales_context["selectedStart"],
        current_end=sales_context["selectedEnd"],
        previous_start=sales_context["comparisonStart"],
        previous_end=sales_context["comparisonEnd"],
        missing_label="Sin canal",
    )
    route_stats = aggregate_dimension(
        filtered_sales,
        "route_description",
        "route_description",
        current_start=sales_context["selectedStart"],
        current_end=sales_context["selectedEnd"],
        previous_start=sales_context["comparisonStart"],
        previous_end=sales_context["comparisonEnd"],
        missing_label="Sin ruta",
    )

    coverage = build_coverage(current_period)
    supplier_focus_data = build_supplier_focus(
        supplier_context_current_period,
        supplier_context_previous_period,
        sales_context,
        supplier_focus,
    )
    summary = build_summary(current_period, previous_period, client_stats, seller_stats, sales_force_stats, family_stats, brand_stats, business_unit_stats, channel_stats, route_stats, coverage, sales_context)
    ratios = build_ratios(current_period, client_stats, seller_stats, sales_force_stats, family_stats, brand_stats, business_unit_stats, channel_stats, coverage, sales_context)
    forecast = build_forecast(current_period, previous_period, sales_context)
    opportunities = build_opportunities(client_stats, sales_force_stats, family_stats, route_stats, coverage, current_period)
    alerts = build_alerts(summary, coverage, opportunities, seller_stats, sales_force_stats, family_stats, brand_stats, channel_stats, sales_context)
    semaphores = build_semaphores(summary, coverage, forecast, sales_context)
    charts = build_charts(current_period, sales_force_stats, seller_stats, brand_stats, channel_stats, coverage, forecast)
    rankings = build_rankings(client_stats, seller_stats, sales_force_stats, route_stats, brand_stats, business_unit_stats, channel_stats, opportunities)
    insights = build_insights(summary, coverage, forecast, opportunities, alerts, sales_context)
    action_plan = build_action_plan(alerts, opportunities, forecast, coverage, sales_context)
    available_filters = build_faceted_available_filters(selected_unfiltered_sales, applied_filters, sales_context)
    schema = detect_schema(current_period)
    tasks = resolve_tasks(schema)
    engine = AnalysisEngine()
    engine_results = engine.run_all(tasks, current_period)
    kpi_set = generate_kpis(engine_results, tasks)
    viz_configs = build_all_viz(tasks, engine_results)
    dyn_insights = write_insights(kpi_set, engine_results, tasks)

    return {
        "meta": {
            "periodStart": sales_context["selectedStart"].isoformat(),
            "periodEnd": max_date.isoformat(),
            "rowsUniverse": len(selected_unfiltered_sales),
            "rowsAnalyzed": len(current_period),
            "comparisonRows": len(previous_period),
            "activeFilterSummary": summarize_filters(applied_filters),
            "datasets": build_dataset_meta(loaded),
            "comparison": {
                "selectedStart": sales_context["selectedStart"].isoformat(),
                "selectedEnd": sales_context["selectedEnd"].isoformat(),
                "selectedLabel": sales_context["selectedLabel"],
                "comparisonStart": sales_context["comparisonStart"].isoformat(),
                "comparisonEnd": sales_context["comparisonEnd"].isoformat(),
                "comparisonLabel": sales_context["comparisonLabel"],
                "days": sales_context["days"],
                "selectedPeriods": sales_context["selectedPeriods"],
            },
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
        "supplierFocus": supplier_focus_data,
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
            "analysisRange": config.get("analysisRange"),
            "comparisonRange": config.get("comparisonRange"),
            "loadRange": config.get("loadRange"),
            "comparisonDays": config.get("comparisonDays"),
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
        "analysisRange": config.get("analysisRange"),
        "comparisonRange": config.get("comparisonRange"),
        "loadRange": config.get("loadRange"),
        "comparisonDays": config.get("comparisonDays"),
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
        product_key = row.get("product_key")
        seller_key = row.get("seller_key")
        route_description = row.get("route_description")
        seller_name_raw = row.get("seller_name")
        article = article_map.get(product_key, {})
        seller_master = seller_key_map.get(seller_key, {}) if seller_key else {}
        if not seller_master and route_description:
            seller_master = seller_route_map.get(normalize_text(route_description), {})
        if not seller_master and seller_name_raw:
            seller_master = seller_name_map.get(normalize_text(seller_name_raw), {})
        seller_name = first_non_empty(seller_name_raw, seller_master.get("seller_name"), seller_key, "Sin vendedor")
        route = route_seller_map.get(normalize_text(seller_name), {})
        sales_force = first_non_empty(row.get("sales_force"), route.get("sales_force"), seller_master.get("sales_force"), "Sin fuerza de ventas")
        route_name = first_non_empty(route_description, route.get("route_description"), seller_master.get("route_description"), "Sin ruta")
        enriched.append(
            {
                **row,
                "client": row.get("client_name") or row.get("client_key"),
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
                "has_seller_match": bool(seller_master or seller_key or seller_name_raw),
            }
        )
    return enriched


def aggregate_clients(records, current_start, current_end, previous_start=None, previous_end=None):
    clients = {}
    grouped = defaultdict(list)
    for item in records:
        grouped[item["client_key"]].append(item)
    for client_key, items in grouped.items():
        relevant_items = [item for item in items if item["date"] <= current_end]
        if not relevant_items:
            continue
        last_date = max(item["date"] for item in relevant_items)
        recency = (current_end - last_date).days
        dates = sorted({item["date"] for item in relevant_items})
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        avg_gap = median(gaps) if gaps else 45
        status = classify_client(recency, avg_gap)
        current_items = [item for item in relevant_items if current_start <= item["date"] <= current_end]
        previous_items = [
            item
            for item in relevant_items
            if previous_start and previous_end and previous_start <= item["date"] <= previous_end
        ]
        reference = current_items or relevant_items
        orders = len({item["invoice"] for item in reference})
        families = {item["family"] for item in reference if item["family"] != "Sin familia"}
        quantity_current = sum(item.get("quantity", 0) or 0 for item in current_items)
        quantity_previous = sum(item.get("quantity", 0) or 0 for item in previous_items)
        quantity_history = sum(item.get("quantity", 0) or 0 for item in relevant_items)
        clients[client_key] = {
            "client_key": client_key,
            "client": relevant_items[-1]["client"],
            "sales12m": round(sum(item["amount"] for item in current_items), 2),
            "salesPrevious": round(sum(item["amount"] for item in previous_items), 2),
            "salesHistory": round(sum(item["amount"] for item in relevant_items), 2),
            "quantity12m": round(quantity_current, 2),
            "quantityPrevious": round(quantity_previous, 2),
            "quantityHistory": round(quantity_history, 2),
            "monthsActive": len({item["date"].strftime("%Y-%m") for item in current_items}),
            "avgTicket": round(sum(item["amount"] for item in reference) / max(orders, 1), 2),
            "avgUnitsPerOrder": round(quantity_current / max(orders, 1), 2),
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


def aggregate_dimension(records, key, label_key, current_start=None, current_end=None, previous_start=None, previous_end=None, missing_label=None):
    stats = {}
    grouped = defaultdict(list)
    for item in records:
        grouped[item.get(key) or missing_label or f"Sin {label_key}"].append(item)
    for name, items in grouped.items():
        current_items = [
            item for item in items
            if current_start and current_end and current_start <= item["date"] <= current_end
        ] if current_start and current_end else items
        previous_items = [
            item for item in items
            if previous_start and previous_end and previous_start <= item["date"] <= previous_end
        ] if previous_start and previous_end else []
        sales = sum(item["amount"] for item in current_items)
        clients = len({item["client_key"] for item in current_items})
        orders = len(order_index(current_items))
        quantity = sum(item.get("quantity", 0) or 0 for item in current_items)
        current_sales = sales
        previous_sales = sum(item["amount"] for item in previous_items)
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


def build_summary(current_period, previous_period, client_stats, seller_stats, sales_force_stats, family_stats, brand_stats, business_unit_stats, channel_stats, route_stats, coverage, period_context):
    current_sales = sum(item["amount"] for item in current_period)
    previous_sales = sum(item["amount"] for item in previous_period)
    previous_units = sum(item.get("quantity", 0) or 0 for item in previous_period)
    total_clients = max(len(client_stats), 1)
    current_orders = order_index(current_period)
    current_units = sum(item.get("quantity", 0) or 0 for item in current_period)
    current_order_count = len(current_orders)
    period_months = period_month_count(period_context)
    avg_order_value = round(current_sales / max(current_order_count, 1), 2)
    avg_unit_price = round(current_sales / max(current_units, 1), 2)
    avg_units_per_order = round(current_units / max(current_order_count, 1), 2)
    active = sum(1 for item in client_stats.values() if item["status"] == "Activo")
    buying_clients = len({item["client_key"] for item in current_period if clean_text(item.get("client_key"))})
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
    volume_mode_active = current_units > 0 or previous_units > 0
    return {
        "volumeModeActive": volume_mode_active,
        "periodLabel": period_context["selectedLabel"],
        "comparisonLabel": period_context["comparisonLabel"],
        "periodDays": period_context["days"],
        "periodPeriods": period_context["selectedPeriods"],
        "salesCurrent": round(current_sales, 2),
        "salesPrevious": round(previous_sales, 2),
        "salesGrowthPct": pct_change(current_sales, previous_sales),
        "unitsPrevious": round(previous_units, 2),
        "unitsGrowthPct": pct_change(current_units, previous_units),
        "ordersCurrent": current_order_count,
        "ordersPerMonth": round(current_order_count / period_months, 1),
        "unitsCurrent": round(current_units, 2),
        "avgOrderValue": avg_order_value,
        "avgUnitPrice": avg_unit_price,
        "avgUnitsPerOrder": avg_units_per_order,
        "activeClients": active,
        "buyingClients": buying_clients,
        "activeRatioPct": round(active / total_clients * 100, 1),
        "purchaseFrequencyMonthly": round(current_order_count / period_months / max(buying_clients, 1), 2),
        "purchaseFrequencyUniverseMonthly": round(current_order_count / period_months / total_clients, 2),
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
        "unitsPerActiveSeller": round(current_units / max(seller_count, 1), 2),
        "topBrandSharePct": round(concentration_share(brand_stats, 1, "sales"), 1),
        "topBusinessUnitSharePct": round(concentration_share(business_unit_stats, 1, "sales"), 1),
        "topChannelSharePct": round(concentration_share(channel_stats, 1, "sales"), 1),
        "portfolioHealthPct": round((active + reactivable * 0.5) / total_clients * 100, 1),
        "routeCoveragePct": coverage["routeCoveragePct"],
        "articleCoveragePct": coverage["articleCoveragePct"],
        "sellerCoveragePct": coverage["sellerCoveragePct"],
    }


def build_ratios(records, client_stats, seller_stats, sales_force_stats, family_stats, brand_stats, business_unit_stats, channel_stats, coverage, period_context=None):
    total_sales = sum(item["amount"] for item in records)
    total_clients = max(len(client_stats), 1)
    total_sellers = max(count_real_dimension_items(seller_stats, "seller"), 1)
    orders = order_index(records)
    total_orders = len(orders)
    if period_context:
        month_count = period_month_count(period_context)
    else:
        month_count = max(len({item["date"].strftime("%Y-%m") for item in records}), 1)
    buying_clients = len({item["client_key"] for item in records if clean_text(item.get("client_key"))})
    safe_orders = max(total_orders, 1)
    total_units = sum(item.get("quantity", 0) or 0 for item in records)
    top_sales_force_share = concentration_share(sales_force_stats, 3, "sales")
    top_seller_share = concentration_share(seller_stats, 3, "sales")
    return {
        "volumeModeActive": total_units > 0,
        "salesPerClient": round(total_sales / total_clients, 2),
        "salesPerSeller": round(total_sales / total_sellers, 2),
        "unitsPerClient": round(total_units / total_clients, 2),
        "unitsPerSeller": round(total_units / total_sellers, 2),
        "clientsPerSeller": round(total_clients / total_sellers, 1),
        "ordersPerSeller": round(safe_orders / total_sellers, 1),
        "totalOrders": total_orders,
        "periodMonths": month_count,
        "totalClients": total_clients,
        "ordersPerMonth": round(total_orders / month_count, 1),
        "purchaseFrequencyMonthly": round(total_orders / month_count / max(buying_clients, 1), 2),
        "purchaseFrequencyUniverseMonthly": round(total_orders / month_count / total_clients, 2),
        "buyingClients": buying_clients,
        "avgOrderValue": round(total_sales / safe_orders, 2),
        "avgUnitsPerOrder": round(total_units / safe_orders, 2),
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


def period_month_count(period_context):
    if not period_context:
        return 1
    periods = period_context.get("selectedPeriods")
    if isinstance(periods, int):
        return max(periods, 1)
    if isinstance(periods, (list, tuple, set)):
        return max(len(periods), 1)
    try:
        return max(int(periods), 1)
    except (TypeError, ValueError):
        return 1


def build_supplier_focus(records, previous_records, period_context, supplier_name):
    selected_supplier = clean_text(supplier_name)
    if not selected_supplier:
        return {
            "selected": False,
            "supplier": "",
            "label": "",
            "ratios": {},
            "monthlyCoverage": [],
        }

    supplier_key = normalize_text(selected_supplier)
    current_supplier_records = [
        item for item in records
        if normalize_text(item.get("supplier")) == supplier_key
    ]
    previous_supplier_records = [
        item for item in previous_records
        if normalize_text(item.get("supplier")) == supplier_key
    ]

    total_active_clients = len({item["client_key"] for item in records if clean_text(item.get("client_key"))})
    supplier_clients = len({item["client_key"] for item in current_supplier_records if clean_text(item.get("client_key"))})
    total_sales = sum(item["amount"] for item in records)
    supplier_sales = sum(item["amount"] for item in current_supplier_records)
    supplier_units = sum(item.get("quantity", 0) or 0 for item in current_supplier_records)
    supplier_orders = len(order_index(current_supplier_records))

    trend_labels = month_labels_between(period_context["comparisonStart"], period_context["selectedEnd"])
    if len(trend_labels) < 2:
        trend_labels = month_labels_between(period_context["selectedStart"], period_context["selectedEnd"])
    if len(trend_labels) < 2:
        selected_label = period_context["selectedEnd"].strftime("%Y-%m")
        comparison_label = period_context["comparisonEnd"].strftime("%Y-%m")
        trend_labels = [comparison_label, selected_label]

    supplier_sales_current_month = aggregate_monthly_sales(current_supplier_records).get(trend_labels[-1], 0)
    supplier_sales_previous_month = aggregate_monthly_sales(previous_supplier_records).get(trend_labels[-2], 0)
    if len(trend_labels) >= 2:
        combined_supplier_sales = aggregate_monthly_sales(current_supplier_records + previous_supplier_records)
        supplier_sales_previous_month = combined_supplier_sales.get(trend_labels[-2], supplier_sales_previous_month)

    monthly_coverage = []
    for label in month_labels_between(period_context["selectedStart"], period_context["selectedEnd"]):
        month_records = [item for item in records if item["date"].strftime("%Y-%m") == label]
        month_supplier_records = [
            item for item in month_records
            if normalize_text(item.get("supplier")) == supplier_key
        ]
        month_active_clients = len({item["client_key"] for item in month_records if clean_text(item.get("client_key"))})
        month_supplier_clients = len({item["client_key"] for item in month_supplier_records if clean_text(item.get("client_key"))})
        monthly_coverage.append(
            {
                "label": label,
                "supplierClients": month_supplier_clients,
                "totalActiveClients": month_active_clients,
                "coveragePct": round(month_supplier_clients / max(month_active_clients, 1) * 100, 1),
            }
        )

    latest_coverage = monthly_coverage[-1] if monthly_coverage else {
        "label": period_context["selectedEnd"].strftime("%Y-%m"),
        "supplierClients": supplier_clients,
        "totalActiveClients": total_active_clients,
        "coveragePct": round(supplier_clients / max(total_active_clients, 1) * 100, 1),
    }

    return {
        "selected": True,
        "supplier": selected_supplier,
        "label": selected_supplier,
        "totals": {
            "sales": round(supplier_sales, 2),
            "units": round(supplier_units, 2),
            "clients": supplier_clients,
            "orders": supplier_orders,
            "totalActiveClients": total_active_clients,
        },
        "ratios": {
            "bultosCliente": round(supplier_units / max(total_active_clients, 1), 2),
            "facturacionCliente": round(supplier_sales / max(total_active_clients, 1), 2),
            "penetracionPct": round(supplier_clients / max(total_active_clients, 1) * 100, 1),
            "rotacion": round(supplier_units / max(total_active_clients, 1), 2),
            "mixMarcaPct": round(supplier_sales / max(total_sales, 1) * 100, 1),
            "ticket": round(supplier_sales / max(supplier_orders, 1), 2),
            "growthPct": pct_change(supplier_sales_current_month, supplier_sales_previous_month),
            "clientesCompradores": supplier_clients,
            "clientesActivosTotales": total_active_clients,
            "facturacionMesActual": round(supplier_sales_current_month, 2),
            "facturacionMesAnterior": round(supplier_sales_previous_month, 2),
            "mesActual": trend_labels[-1],
            "mesAnterior": trend_labels[-2],
            "coberturaClientesActualPct": latest_coverage["coveragePct"],
        },
        "monthlyCoverage": monthly_coverage,
    }


def build_forecast(records, previous_period, period_context):
    monthly_sales = aggregate_monthly_sales(records)
    monthly_units = aggregate_monthly_quantity(records)
    labels = month_labels_between(period_context["selectedStart"], period_context["selectedEnd"])
    if not labels:
        labels = sorted(set(monthly_sales) | set(monthly_units))
    sales_values = [monthly_sales.get(label, 0) for label in labels]
    unit_values = [monthly_units.get(label, 0) for label in labels]
    current_sales_total = sum(item["amount"] for item in records)
    previous_sales_total = sum(item["amount"] for item in previous_period)
    current_units_total = sum(item.get("quantity", 0) or 0 for item in records)
    previous_units_total = sum(item.get("quantity", 0) or 0 for item in previous_period)
    period_count = max(len(labels), 1)

    sales_trend_pct = pct_change(current_sales_total, previous_sales_total)
    moderated_sales_trend = max(min(sales_trend_pct, 20), -20) * 0.6
    projected_period_sales = current_sales_total * (1 + moderated_sales_trend / 100)

    units_trend_pct = pct_change(current_units_total, previous_units_total)
    moderated_units_trend = max(min(units_trend_pct, 20), -20) * 0.6
    projected_period_units = current_units_total * (1 + moderated_units_trend / 100)

    next_labels = next_month_labels(labels[-1] if labels else date.today().strftime("%Y-%m"), period_count)
    forecast_sales_value = projected_period_sales / max(period_count, 1)
    forecast_units_value = projected_period_units / max(period_count, 1)
    forecast_points_sales = [{"label": label, "value": round(forecast_sales_value, 2)} for label in next_labels]
    forecast_points_units = [{"label": label, "value": round(forecast_units_value, 2)} for label in next_labels]
    return {
        "volumeModeActive": any(value > 0 for value in unit_values),
        "windowLabel": period_context["selectedLabel"],
        "nextWindowLabel": period_context["nextLabel"],
        "windowPeriods": period_count,
        "baseMonthlySales": round(current_sales_total / max(period_count, 1), 2),
        "baseMonthlyUnits": round(current_units_total / max(period_count, 1), 2),
        "trendPct": round(sales_trend_pct, 1),
        "unitsTrendPct": round(units_trend_pct, 1),
        "projectedQuarterSales": round(projected_period_sales, 2),
        "projectedQuarterUnits": round(projected_period_units, 2),
        "series": [{"label": label, "value": round(monthly_sales.get(label, 0), 2)} for label in labels],
        "seriesUnits": [{"label": label, "value": round(monthly_units.get(label, 0), 2)} for label in labels],
        "forecastSeries": forecast_points_sales,
        "forecastSeriesUnits": forecast_points_units,
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


def build_alerts(summary, coverage, opportunities, seller_stats, sales_force_stats, family_stats, brand_stats, channel_stats, period_context):
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
        alerts.append(alert("Mix", f"{family['family']} en retracción", f"La familia cae {family['growthPct']}% contra {period_context['comparisonLabel'].lower()}.", "medium"))
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


def build_semaphores(summary, coverage, forecast, period_context):
    return [
        semaphore(
            "Evolución del período",
            color_by_value(summary["unitsGrowthPct"] if summary["volumeModeActive"] else summary["salesGrowthPct"], [0, 8]),
            f"{period_context['selectedLabel']}: {summary['unitsGrowthPct'] if summary['volumeModeActive'] else summary['salesGrowthPct']}% vs {period_context['comparisonLabel'].lower()}"
        ),
        semaphore("Calidad de cartera", color_by_value(summary["portfolioHealthPct"], [65, 80]), f"Cartera sana: {summary['portfolioHealthPct']}%"),
        semaphore("Cobertura de rutas", color_by_value(coverage["routeCoveragePct"], [85, 95]), f"Ventas mapeadas a rutas: {coverage['routeCoveragePct']}%"),
        semaphore("Cobertura de artículos", color_by_value(coverage["articleCoveragePct"], [85, 95]), f"Ventas enriquecidas con maestro: {coverage['articleCoveragePct']}%"),
        semaphore("Cobertura de vendedores", color_by_value(coverage["sellerCoveragePct"], [80, 95]), f"Ventas con maestro de vendedor: {coverage['sellerCoveragePct']}%"),
        semaphore(
            "Proyección",
            color_by_value(forecast["unitsTrendPct"] if forecast["volumeModeActive"] else forecast["trendPct"], [0, 8]),
            forecast["volumeModeActive"]
                and f"Próxima ventana: {int(round(forecast['projectedQuarterUnits']))} bultos"
                or f"Próxima ventana: {money(forecast['projectedQuarterSales'])}"
        ),
    ]


def build_charts(records, sales_force_stats, seller_stats, brand_stats, channel_stats, coverage, forecast):
    return {
        "salesByMonthMoney": forecast["series"],
        "salesByMonthUnits": forecast["seriesUnits"],
        "salesForecastMoney": forecast["forecastSeries"],
        "salesForecastUnits": forecast["forecastSeriesUnits"],
        "salesForceMoney": top_items(sales_force_stats, "sales", "sales_force"),
        "salesForceUnits": top_items(sales_force_stats, "quantity", "sales_force"),
        "sellerProductivityMoney": top_items(seller_stats, "avgOrderValue", "seller"),
        "sellerProductivityUnits": top_items(seller_stats, "quantity", "seller"),
        "brandMoney": top_items(brand_stats, "sales", "brand"),
        "brandUnits": top_items(brand_stats, "quantity", "brand"),
        "channelMoney": top_items(channel_stats, "sales", "channel"),
        "channelUnits": top_items(channel_stats, "quantity", "channel"),
    }


def build_rankings(client_stats, seller_stats, sales_force_stats, route_stats, brand_stats, business_unit_stats, channel_stats, opportunities):
    volume_mode_active = any(item.get("quantity12m", 0) > 0 for item in client_stats.values())
    positive_clients_by_sales = sorted(client_stats.values(), key=lambda item: item["sales12m"], reverse=True)[:10]
    positive_clients_by_units = sorted(client_stats.values(), key=lambda item: item["quantity12m"], reverse=True)[:10]
    risk_clients = sorted(client_stats.values(), key=lambda item: (status_rank(item["status"]), -item["salesHistory"]), reverse=False)[:10]
    top_sellers_by_sales = sorted(seller_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_sellers_by_units = sorted(seller_stats.values(), key=lambda item: item["quantity"], reverse=True)[:10]
    productive_sellers_by_sales = sorted(seller_stats.values(), key=lambda item: item["avgOrderValue"], reverse=True)[:10]
    productive_sellers_by_units = sorted(seller_stats.values(), key=lambda item: item["avgUnitsPerOrder"], reverse=True)[:10]
    top_sales_forces_by_sales = sorted(sales_force_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_sales_forces_by_units = sorted(sales_force_stats.values(), key=lambda item: item["quantity"], reverse=True)[:10]
    top_routes_by_sales = sorted(route_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_routes_by_units = sorted(route_stats.values(), key=lambda item: item["quantity"], reverse=True)[:10]
    top_brands_by_sales = sorted(brand_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_brands_by_units = sorted(brand_stats.values(), key=lambda item: item["quantity"], reverse=True)[:10]
    top_business_units_by_sales = sorted(business_unit_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_business_units_by_units = sorted(business_unit_stats.values(), key=lambda item: item["quantity"], reverse=True)[:10]
    top_channels_by_sales = sorted(channel_stats.values(), key=lambda item: item["sales"], reverse=True)[:10]
    top_channels_by_units = sorted(channel_stats.values(), key=lambda item: item["quantity"], reverse=True)[:10]
    return {
        "volumeModeActive": volume_mode_active,
        "positiveClients": positive_clients_by_units if volume_mode_active else positive_clients_by_sales,
        "positiveClientsBySales": positive_clients_by_sales,
        "positiveClientsByUnits": positive_clients_by_units,
        "riskClients": risk_clients,
        "topSellers": top_sellers_by_units if volume_mode_active else top_sellers_by_sales,
        "topSellersBySales": top_sellers_by_sales,
        "topSellersByUnits": top_sellers_by_units,
        "productiveSellers": productive_sellers_by_units if volume_mode_active else productive_sellers_by_sales,
        "productiveSellersBySales": productive_sellers_by_sales,
        "productiveSellersByUnits": productive_sellers_by_units,
        "topSalesForces": top_sales_forces_by_units if volume_mode_active else top_sales_forces_by_sales,
        "topSalesForcesBySales": top_sales_forces_by_sales,
        "topSalesForcesByUnits": top_sales_forces_by_units,
        "topRoutes": top_routes_by_units if volume_mode_active else top_routes_by_sales,
        "topRoutesBySales": top_routes_by_sales,
        "topRoutesByUnits": top_routes_by_units,
        "topBrands": top_brands_by_units if volume_mode_active else top_brands_by_sales,
        "topBrandsBySales": top_brands_by_sales,
        "topBrandsByUnits": top_brands_by_units,
        "topBusinessUnits": top_business_units_by_units if volume_mode_active else top_business_units_by_sales,
        "topBusinessUnitsBySales": top_business_units_by_sales,
        "topBusinessUnitsByUnits": top_business_units_by_units,
        "topChannels": top_channels_by_units if volume_mode_active else top_channels_by_sales,
        "topChannelsBySales": top_channels_by_sales,
        "topChannelsByUnits": top_channels_by_units,
        "opportunityHeadline": f"Potencial estimado capturable: {money(opportunities['totalPotential'])}",
    }


def build_insights(summary, coverage, forecast, opportunities, alerts, period_context):
    insights = [
        (
            f"En {period_context['selectedLabel'].lower()} el volumen {growth_phrase(summary['unitsGrowthPct'])}, con {summary['ordersCurrent']} pedidos y {int(round(summary['unitsCurrent']))} bultos frente a {period_context['comparisonLabel'].lower()}."
            if summary["volumeModeActive"]
            else f"En {period_context['selectedLabel'].lower()} la venta {growth_phrase(summary['salesGrowthPct'])}, con {summary['ordersCurrent']} pedidos y un ticket promedio de {money(summary['avgOrderValue'])} frente a {period_context['comparisonLabel'].lower()}."
        ),
        (
            f"La productividad comercial actual equivale a {round(summary['unitsPerActiveSeller'], 1)} bultos por vendedor activo, con {summary['avgUnitsPerOrder']} bultos por pedido y {money(summary['avgUnitPrice'])} por bulto."
            if summary["volumeModeActive"]
            else f"La productividad comercial actual equivale a {money(summary['salesPerActiveSeller'])} por vendedor activo, con un precio medio de {money(summary['avgUnitPrice'])} por unidad."
        ),
        f"El mix activo hoy cubre {summary['brandCount']} marcas, {summary['businessUnitCount']} unidades de negocio y {summary['channelCount']} canales. La marca líder concentra {summary['topBrandSharePct']}% y el canal principal {summary['topChannelSharePct']}%.",
        f"La calidad de datos para BI queda en rutas {coverage['routeCoveragePct']}%, artículos {coverage['articleCoveragePct']}% y vendedores {coverage['sellerCoveragePct']}% de cobertura sobre ventas.",
        (
            f"La proyección base para {forecast['nextWindowLabel'].lower()} es {int(round(forecast['projectedQuarterUnits']))} bultos, apoyada en una base media de {int(round(forecast['baseMonthlyUnits']))} bultos por período y una tendencia de {forecast['unitsTrendPct']}%."
            if forecast["volumeModeActive"]
            else f"La proyección base para {forecast['nextWindowLabel'].lower()} es {money(forecast['projectedQuarterSales'])}, apoyada en una base media de {money(forecast['baseMonthlySales'])} por período y una tendencia de {forecast['trendPct']}%."
        ),
        f"Las palancas más claras hoy son recuperar cartera dormida ({money(opportunities['recoverDormantSales'])}), venta cruzada ({money(opportunities['crossSellPotential'])}) y optimización de ruteo ({money(opportunities['routeOptimizationPotential'])}).",
    ]
    if alerts:
        insights.append(f"La alerta principal hoy es {alerts[0]['title'].lower()}: {alerts[0]['detail']}")
    return insights[:6]


def build_action_plan(alerts, opportunities, forecast, coverage, period_context):
    actions = []
    if opportunities["recoverDormantSales"] > 0:
        actions.append(action("Plan de recuperación de cartera", "Alta", "Comercial", "15 días", f"Priorizar dormidos y reactivables con potencial de {money(opportunities['recoverDormantSales'])}."))
    if opportunities["crossSellPotential"] > 0:
        actions.append(action("Plan de profundización de surtido", "Alta", "Ventas + trade", "30 días", f"Trabajar clientes activos de baja profundidad para capturar {money(opportunities['crossSellPotential'])}."))
    if coverage["routeCoveragePct"] < 95:
        actions.append(action("Depurar maestro de rutas", "Media", "Administración comercial", "10 días", f"Hay {money(coverage['salesWithoutRoute'])} de venta sin ruta asignada."))
    if coverage["articleCoveragePct"] < 95:
        actions.append(action("Completar maestro de artículos", "Media", "Producto / sistemas", "10 días", f"Hay {money(coverage['salesWithoutArticle'])} de venta sin enriquecer con familia, marca o línea."))
    actions.append(action("Revisar proyección de la próxima ventana", "Alta", "Dirección comercial", "Próximo comité", f"Tomar la base proyectada de {money(forecast['projectedQuarterSales'])} para {forecast['nextWindowLabel'].lower()} y convertirla en meta por zona y vendedor."))
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


def resolve_period_context(sales_dataset, records):
    analysis_range = sales_dataset.get("analysisRange") or {}
    comparison_range = sales_dataset.get("comparisonRange") or {}

    selected_start = parse_date(analysis_range.get("fechaDesde")) if analysis_range else None
    selected_end = parse_date(analysis_range.get("fechaHasta")) if analysis_range else None
    if not selected_start or not selected_end:
        selected_start = min(item["date"] for item in records)
        selected_end = max(item["date"] for item in records)

    comparison_start = parse_date(comparison_range.get("fechaDesde")) if comparison_range else None
    comparison_end = parse_date(comparison_range.get("fechaHasta")) if comparison_range else None
    if not comparison_start or not comparison_end:
        days = max((selected_end - selected_start).days + 1, 1)
        comparison_end = selected_start - timedelta(days=1)
        comparison_start = comparison_end - timedelta(days=days - 1)

    selected_labels = month_labels_between(selected_start, selected_end)
    if not selected_labels:
        selected_labels = sorted({item["date"].strftime("%Y-%m") for item in records_between(records, selected_start, selected_end)})
    next_labels = next_month_labels(selected_labels[-1] if selected_labels else selected_end.strftime("%Y-%m"), max(len(selected_labels), 1))
    return {
        "selectedStart": selected_start,
        "selectedEnd": selected_end,
        "comparisonStart": comparison_start,
        "comparisonEnd": comparison_end,
        "days": max((selected_end - selected_start).days + 1, 1),
        "selectedLabel": format_date_range(selected_start, selected_end),
        "comparisonLabel": format_date_range(comparison_start, comparison_end),
        "nextLabel": format_date_range_for_labels(next_labels),
        "selectedPeriods": len(selected_labels) or 1,
    }


def records_between(records, start, end):
    return [item for item in records if start <= item["date"] <= end]


def format_date_range(start, end):
    if start == end:
        return start.strftime("%d/%m/%Y")
    return f"{start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}"


def format_date_range_for_labels(labels):
    if not labels:
        return "la próxima ventana"
    if len(labels) == 1:
        year_value, month_value = [int(value) for value in labels[0].split("-")]
        return f"{MONTH_NAMES.get(month_value, month_value)} {year_value}"
    first_year, first_month = [int(value) for value in labels[0].split("-")]
    last_year, last_month = [int(value) for value in labels[-1].split("-")]
    return f"{MONTH_NAMES.get(first_month, first_month)} {first_year} al {MONTH_NAMES.get(last_month, last_month)} {last_year}"


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


def build_available_filters(records, period_context=None):
    filters = {}
    for field, meta in FILTER_DEFINITIONS.items():
        config = build_filter_config(records, field, meta, period_context)
        if config:
            filters[field] = config
    return filters


def build_faceted_available_filters(records, applied_filters=None, period_context=None):
    filters = {}
    applied_filters = applied_filters or {}
    for field, meta in FILTER_DEFINITIONS.items():
        facet_filters = remove_filter_field(applied_filters, field)
        _, facet_records = apply_filters(records, facet_filters)
        config = build_filter_config(facet_records, field, meta, period_context)
        if config:
            filters[field] = {
                **config,
                "constrainedBy": sorted(f for f in facet_filters if facet_filters.get(f)),
            }
    return filters


def build_filter_config(records, field, meta, period_context=None):
    counter = Counter()
    labels = {}
    for item in records:
        value = filter_value(item, field)
        if value in (None, ""):
            continue
        counter[value] += 1
        labels[value] = filter_label(field, value)
    ordered_values = list(sort_filter_values(field, counter.keys()))
    if field in {"year", "month"} and period_context:
        period_values = period_filter_values(field, period_context["selectedStart"], period_context["selectedEnd"])
        for value in period_values:
            counter.setdefault(value, 0)
            labels[value] = filter_label(field, value)
        ordered_values = list(sort_filter_values(field, counter.keys()))
    options = [
        {
            "value": value,
            "label": labels[value],
            "count": counter[value],
        }
        for value in ordered_values
    ]
    if not options:
        return None
    return {
        "label": meta["label"],
        "kind": meta["kind"],
        "options": options,
    }


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


def remove_filter_field(raw_filters, field):
    return {
        key: value
        for key, value in (raw_filters or {}).items()
        if key != field
    }


def remove_noop_filters(raw_filters, universe_records, period_context=None):
    if not raw_filters:
        return {}
    cleaned = {}
    available = build_available_filter_values(universe_records, period_context)
    for field, raw_values in (raw_filters or {}).items():
        if field not in FILTER_DEFINITIONS:
            continue
        values = normalize_filter_values(field, raw_values)
        if not values:
            cleaned[field] = values
            continue
        available_values = available.get(field, set())
        selected_markers = {filter_compare_marker(field, value) for value in values}
        if available_values and selected_markers >= available_values:
            continue
        cleaned[field] = values
    return cleaned


def relax_broad_filters(raw_filters, universe_records, period_context=None):
    if not raw_filters:
        return {}
    available = build_available_filter_values(universe_records, period_context)
    relaxed = {}
    for field, raw_values in (raw_filters or {}).items():
        values = normalize_filter_values(field, raw_values)
        if not values:
            relaxed[field] = values
            continue
        available_values = available.get(field, set())
        selected_markers = {filter_compare_marker(field, value) for value in values}
        selected_count = len(selected_markers)
        available_count = len(available_values)
        broad_route_filter = field == "route_description" and selected_count >= 25
        broad_dimension_filter = available_count and selected_count / max(available_count, 1) >= 0.85
        if broad_route_filter or broad_dimension_filter:
            continue
        relaxed[field] = values
    return relaxed


def build_available_filter_values(records, period_context=None):
    available = {}
    for field in FILTER_DEFINITIONS:
        values = {
            filter_compare_marker(field, value)
            for value in (filter_value(item, field) for item in records)
            if value not in (None, "")
        }
        if field in {"year", "month"} and period_context:
            values.update(
                filter_compare_marker(field, value)
                for value in period_filter_values(field, period_context["selectedStart"], period_context["selectedEnd"])
            )
        if values:
            available[field] = values
    return available


def filter_compare_marker(field, value):
    if field in {"year", "month"}:
        parsed = parse_year(value) if field == "year" else parse_month(value)
        return parsed
    return normalize_text(value)


def merge_supplier_focus_filter(raw_filters, supplier_focus):
    merged = dict(raw_filters or {})
    selected_supplier = clean_text(supplier_focus)
    if not selected_supplier:
        return merged

    existing_values = normalize_filter_values("supplier", merged.get("supplier"))
    if not existing_values:
        merged["supplier"] = [selected_supplier]
        return merged

    merged["supplier"] = [
        value for value in existing_values
        if normalize_text(value) == normalize_text(selected_supplier)
    ]
    return merged


def period_filter_values(field, start, end):
    values = []
    cursor = date(start.year, start.month, 1)
    end_marker = date(end.year, end.month, 1)
    while cursor <= end_marker:
        values.append(cursor.year if field == "year" else cursor.month)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    if field == "year":
        return sorted(set(values), reverse=True)
    return sorted(set(values))


def month_labels_between(start, end):
    if not start or not end or start > end:
        return []
    labels = []
    cursor = date(start.year, start.month, 1)
    end_marker = date(end.year, end.month, 1)
    while cursor <= end_marker:
        labels.append(cursor.strftime("%Y-%m"))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return labels


def aggregate_monthly_sales(records):
    monthly = defaultdict(float)
    for item in records:
        monthly[item["date"].strftime("%Y-%m")] += item["amount"]
    return monthly


def aggregate_monthly_quantity(records):
    monthly = defaultdict(float)
    for item in records:
        monthly[item["date"].strftime("%Y-%m")] += item.get("quantity", 0) or 0
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
