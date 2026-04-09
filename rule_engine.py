"""
rule_engine.py — Fase 2 del motor dinámico de análisis comercial.

Responsabilidad:
    Dado un SchemaProfile (salida de schema_detector), determina:

    1. Qué análisis son posibles (AnalysisTask) → cada uno sabe exactamente
       qué campos del dataset usar, sin hardcodear nombres.

    2. Con qué combinaciones de dimensiones puede ejecutarse cada uno
       (ComboSpec) → base para el selector dinámico del frontend.

    3. Prioridad de ejecución y agrupación por dominio → el frontend puede
       presentar los análisis ordenados por relevancia.

Estructuras clave:
    AnalysisTask = {
        "id":          "temporal_trend",        # id canónico
        "label":       "Evolución temporal",
        "domain":      "tiempo",                # tiempo | cartera | territorio | producto | canal | margen
        "priority":    1,                       # 1=alta, 2=media, 3=baja
        "fields": {
            "date":      "date",               # nombre real del campo en el dataset
            "metric":    "amount",
            "dimension": "seller_name",        # opcional según análisis
        },
        "combos": [                             # combinaciones habilitadas
            {"dim_a": "seller_name", "dim_b": None,          "label": "Por vendedor"},
            {"dim_a": "sales_force", "dim_b": None,          "label": "Por fuerza de ventas"},
            {"dim_a": "seller_name", "dim_b": "sales_force", "label": "Vendedor × Fuerza"},
        ],
        "viz_hint":    "line",                  # hint para viz_selector (Fase 4)
        "insight_tags": ["concentracion","crecimiento"]  # hint para insight_writer (Fase 5)
    }

Uso:
    from schema_detector import detect_schema
    from rule_engine import resolve_tasks

    profile = detect_schema(enriched_records)
    tasks   = resolve_tasks(profile)
    # tasks es una lista de AnalysisTask lista para pasar al AnalysisEngine (Fase 3)
"""

from schema_detector import ANALYSIS_RULES as _SCHEMA_RULES

# ---------------------------------------------------------------------------
# Catálogo completo de reglas con metadatos extendidos
# ---------------------------------------------------------------------------
# Cada entrada amplía las reglas básicas de schema_detector con:
#   domain, priority, viz_hint, insight_tags,
#   combo_dims: qué sub-roles de DIMENSIÓN pueden combinarse
#   single_dims: sub-roles válidos para análisis de una sola dimensión

_RULE_CATALOG = {
    "temporal_trend": {
        "domain":       "tiempo",
        "priority":     1,
        "viz_hint":     "line",
        "insight_tags": ["crecimiento", "tendencia", "estacionalidad"],
        "field_map": {
            "date":   {"subroles": ["FECHA"],      "fallback_subroles": ["FECHA_AÑO", "FECHA_MES"]},
            "metric": {"subroles": ["MET_VENTA"],  "fallback_subroles": ["MET_CANTIDAD"]},
        },
        "combo_dims": [
            "DIM_VENDEDOR", "DIM_FUERZA", "DIM_CANAL",
            "DIM_FAMILIA",  "DIM_RUTA",   "DIM_CLIENTE",
        ],
    },
    "dimension_ranking": {
        "domain":       "cartera",
        "priority":     1,
        "viz_hint":     "bar",
        "insight_tags": ["concentracion", "ranking", "participacion"],
        "field_map": {
            "metric": {"subroles": ["MET_VENTA"], "fallback_subroles": ["MET_CANTIDAD"]},
        },
        "combo_dims": [
            "DIM_CLIENTE",  "DIM_VENDEDOR", "DIM_FUERZA",
            "DIM_FAMILIA",  "DIM_RUTA",     "DIM_CANAL",
            "DIM_PRODUCTO", "DIM_PROVEEDOR","DIM_LINEA",
        ],
        "single_dims": True,     # puede usarse con una sola dimensión
        "cross_dims":  True,     # puede cruzar dos dimensiones
    },
    "recurrence_churn": {
        "domain":       "cartera",
        "priority":     1,
        "viz_hint":     "stacked_bar",
        "insight_tags": ["churn", "recurrencia", "cartera_dormida"],
        "field_map": {
            "date":   {"subroles": ["FECHA"]},
            "client": {"subroles": ["DIM_CLIENTE"], "fallback_subroles": ["ID_CLIENTE"]},
            "metric": {"subroles": ["MET_VENTA"],   "fallback_subroles": ["MET_CANTIDAD"]},
        },
        "combo_dims": ["DIM_VENDEDOR", "DIM_FUERZA", "DIM_RUTA", "DIM_CANAL"],
    },
    "cross_sell_mix": {
        "domain":       "producto",
        "priority":     2,
        "viz_hint":     "heatmap",
        "insight_tags": ["mix", "profundidad", "oportunidad_surtido"],
        "field_map": {
            "client":  {"subroles": ["DIM_CLIENTE"],  "fallback_subroles": ["ID_CLIENTE"]},
            "product": {"subroles": ["DIM_FAMILIA"],  "fallback_subroles": ["DIM_LINEA", "DIM_PRODUCTO"]},
        },
        "combo_dims": ["DIM_VENDEDOR", "DIM_FUERZA", "DIM_CANAL"],
    },
    "seller_performance": {
        "domain":       "territorio",
        "priority":     1,
        "viz_hint":     "bar",
        "insight_tags": ["vendedor_bajo", "concentracion_comercial"],
        "field_map": {
            "seller": {"subroles": ["DIM_VENDEDOR"]},
            "metric": {"subroles": ["MET_VENTA"], "fallback_subroles": ["MET_CANTIDAD"]},
        },
        "combo_dims": ["DIM_FUERZA", "DIM_RUTA", "DIM_CANAL"],
    },
    "geographic_coverage": {
        "domain":       "territorio",
        "priority":     2,
        "viz_hint":     "bar_horizontal",
        "insight_tags": ["cobertura_ruta", "brechas_territoriales"],
        "field_map": {
            "route":  {"subroles": ["DIM_RUTA"]},
            "metric": {"subroles": ["MET_VENTA"], "fallback_subroles": ["MET_CANTIDAD"]},
        },
        "combo_dims": ["DIM_FUERZA", "DIM_VENDEDOR"],
    },
    "margin_analysis": {
        "domain":       "margen",
        "priority":     1,
        "viz_hint":     "scatter",
        "insight_tags": ["margen", "rentabilidad", "bajo_margen"],
        "field_map": {
            "revenue": {"subroles": ["MET_VENTA"]},
            "cost":    {"subroles": ["MET_COSTO"]},
        },
        "combo_dims": [
            "DIM_CLIENTE", "DIM_FAMILIA", "DIM_PRODUCTO",
            "DIM_VENDEDOR", "DIM_CANAL",
        ],
    },
    "sales_force_breakdown": {
        "domain":       "territorio",
        "priority":     2,
        "viz_hint":     "bar",
        "insight_tags": ["fuerza_ventas", "concentracion_comercial"],
        "field_map": {
            "sales_force": {"subroles": ["DIM_FUERZA"]},
            "metric":      {"subroles": ["MET_VENTA"], "fallback_subroles": ["MET_CANTIDAD"]},
        },
        "combo_dims": ["DIM_VENDEDOR", "DIM_RUTA", "DIM_CANAL"],
    },
    "product_analysis": {
        "domain":       "producto",
        "priority":     2,
        "viz_hint":     "bar",
        "insight_tags": ["familia_en_retraccion", "mix", "participacion_producto"],
        "field_map": {
            "product": {"subroles": ["DIM_FAMILIA"], "fallback_subroles": ["DIM_LINEA", "DIM_PRODUCTO"]},
            "metric":  {"subroles": ["MET_VENTA"],   "fallback_subroles": ["MET_CANTIDAD"]},
        },
        "combo_dims": ["DIM_PROVEEDOR", "DIM_LINEA", "DIM_CANAL", "DIM_CLIENTE"],
    },
    "channel_analysis": {
        "domain":       "canal",
        "priority":     2,
        "viz_hint":     "donut",
        "insight_tags": ["participacion_canal", "concentracion"],
        "field_map": {
            "channel": {"subroles": ["DIM_CANAL"]},
            "metric":  {"subroles": ["MET_VENTA"], "fallback_subroles": ["MET_CANTIDAD"]},
        },
        "combo_dims": ["DIM_FAMILIA", "DIM_VENDEDOR", "DIM_FUERZA"],
    },
}

# Orden de dominio para presentación en el frontend
_DOMAIN_ORDER = ["tiempo", "cartera", "territorio", "producto", "canal", "margen"]


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def resolve_tasks(schema_profile):
    """
    Produce la lista de AnalysisTask para un SchemaProfile.

    Cada AnalysisTask contiene:
      - Los campos reales del dataset (sin nombres hardcodeados)
      - Las combinaciones de dimensiones habilitadas (combos)
      - Metadatos para visualización e insights

    Args:
        schema_profile: dict — salida de detect_schema() o detect_schema_from_raw()

    Returns:
        list[AnalysisTask] — ordenada por dominio y prioridad
    """
    available_ids = {a["id"] for a in schema_profile.get("available_analyses", [])}
    subroles = schema_profile.get("subroles", {})

    tasks = []
    for analysis_id, catalog in _RULE_CATALOG.items():
        if analysis_id not in available_ids:
            continue

        # Resolver los campos reales para este análisis
        resolved_fields = _resolve_fields(catalog["field_map"], subroles)
        if resolved_fields is None:
            continue  # algún campo requerido no pudo resolverse

        # Calcular combos de dimensiones habilitadas
        combos = _build_combos(catalog, subroles, schema_profile)

        # Buscar label en los ANALYSIS_RULES del schema_detector
        label = _label_for(analysis_id)

        task = {
            "id":           analysis_id,
            "label":        label,
            "domain":       catalog["domain"],
            "priority":     catalog["priority"],
            "fields":       resolved_fields,
            "combos":       combos,
            "viz_hint":     catalog["viz_hint"],
            "insight_tags": catalog["insight_tags"],
        }
        tasks.append(task)

    tasks.sort(key=lambda t: (_DOMAIN_ORDER.index(t["domain"]) if t["domain"] in _DOMAIN_ORDER else 99, t["priority"]))
    return tasks


def tasks_by_domain(tasks):
    """
    Agrupa la lista de AnalysisTask por dominio.

    Returns:
        dict[domain -> list[AnalysisTask]]
    """
    grouped = {}
    for task in tasks:
        domain = task["domain"]
        grouped.setdefault(domain, []).append(task)
    return grouped


def tasks_summary(tasks):
    """
    Resumen compacto de los tasks para incluir en el JSON de respuesta.
    No incluye `combos` para mantener el payload liviano.
    """
    return [
        {
            "id":       t["id"],
            "label":    t["label"],
            "domain":   t["domain"],
            "priority": t["priority"],
            "viz_hint": t["viz_hint"],
            "fields":   t["fields"],
            "combos":   [
                {"dim_a": c.get("dim_a"), "dim_b": c.get("dim_b"), "label": c.get("label", "")}
                for c in t.get("combos", [])
            ],
        }
        for t in tasks
    ]


# ---------------------------------------------------------------------------
# Resolución de campos
# ---------------------------------------------------------------------------

def _resolve_fields(field_map, subroles):
    """
    Para cada slot del field_map, busca el campo real en subroles.
    Intenta primero con los subroles primarios, luego con fallbacks.
    Devuelve None si algún slot requerido no se puede resolver.
    """
    resolved = {}
    for slot, spec in field_map.items():
        field = _find_field(spec.get("subroles", []), subroles)
        if field is None and spec.get("fallback_subroles"):
            field = _find_field(spec["fallback_subroles"], subroles)
        if field is None:
            # Si el subrole primario es FECHA (puede ser date_field), lo ignoramos
            # como requerido estricto solo cuando hay fallback
            if not spec.get("fallback_subroles"):
                return None  # campo requerido sin resolver
        resolved[slot] = field
    return resolved


def _find_field(subrole_list, subroles):
    """Devuelve el nombre real del campo que tiene alguno de los sub-roles dados."""
    for subrole in subrole_list:
        field = subroles.get(subrole)
        if field:
            return field
    return None


# ---------------------------------------------------------------------------
# Construcción de combos de dimensiones
# ---------------------------------------------------------------------------

def _build_combos(catalog, subroles, profile):
    """
    Para cada análisis, construye las combinaciones de dimensión disponibles:
    - Combos simples (una dimensión)
    - Combos cruzados de 2 dimensiones (si cross_dims=True)

    Solo incluye dimensiones que tengan cobertura mínima (≥ 10% de filas).
    """
    combo_subroles = catalog.get("combo_dims", [])
    fields_info = profile.get("fields", {})
    combos = []
    available_dims = []

    for subrole in combo_subroles:
        field = subroles.get(subrole)
        if not field:
            continue
        field_stats = fields_info.get(field, {})
        coverage = field_stats.get("coverage", 0)
        if coverage < 0.10:
            continue
        cardinality = field_stats.get("cardinality") or 0
        available_dims.append({
            "field":       field,
            "subrole":     subrole,
            "coverage":    coverage,
            "cardinality": cardinality,
            "label":       _dim_label(subrole),
        })

    # Combos simples
    for dim in available_dims:
        combos.append({
            "dim_a":    dim["field"],
            "dim_b":    None,
            "label":    f"Por {dim['label']}",
            "subrole_a": dim["subrole"],
            "subrole_b": None,
        })

    # Combos cruzados (solo si cross_dims = True)
    if catalog.get("cross_dims") and len(available_dims) >= 2:
        for i, dim_a in enumerate(available_dims):
            for dim_b in available_dims[i + 1:]:
                combos.append({
                    "dim_a":    dim_a["field"],
                    "dim_b":    dim_b["field"],
                    "label":    f"{dim_a['label']} × {dim_b['label']}",
                    "subrole_a": dim_a["subrole"],
                    "subrole_b": dim_b["subrole"],
                })

    return combos


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

_SUBROLE_LABELS = {
    "DIM_CLIENTE":   "cliente",
    "DIM_VENDEDOR":  "vendedor",
    "DIM_FUERZA":    "fuerza de ventas",
    "DIM_RUTA":      "ruta",
    "DIM_FAMILIA":   "familia",
    "DIM_CANAL":     "canal",
    "DIM_PRODUCTO":  "producto",
    "DIM_LINEA":     "línea",
    "DIM_PROVEEDOR": "proveedor",
    "DIM_SABOR":     "sabor",
    "DIM_CALIBRE":   "calibre",
    "DIM_UXB":       "UxB",
    "MET_VENTA":     "importe",
    "MET_CANTIDAD":  "cantidad",
    "MET_COSTO":     "costo",
    "FECHA":         "fecha",
    "FECHA_AÑO":     "año",
    "FECHA_MES":     "mes",
}


def _dim_label(subrole):
    return _SUBROLE_LABELS.get(subrole, subrole.lower().replace("dim_", ""))


def _label_for(analysis_id):
    for rule in _SCHEMA_RULES:
        if rule["id"] == analysis_id:
            return rule["label"]
    return analysis_id
