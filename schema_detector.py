"""
schema_detector.py — Fase 1 del motor dinámico de análisis comercial.

Responsabilidad:
    Analizar un conjunto de registros ya normalizados (o headers + muestras crudas)
    y producir un SchemaProfile con el rol semántico de cada campo, cobertura,
    cardinalidad y lista de análisis disponibles.

Roles:
    FECHA        → campo temporal
    DIMENSIÓN    → atributo categórico segmentable
    MÉTRICA      → valor numérico sumable
    IDENTIFICADOR → clave de registro / ID

Sub-roles refinan DIMENSIÓN y MÉTRICA para activar análisis específicos:
    DIM_CLIENTE, DIM_VENDEDOR, DIM_PRODUCTO, DIM_FAMILIA,
    DIM_CANAL, DIM_RUTA, DIM_FUERZA, DIM_LINEA, DIM_PROVEEDOR
    MET_VENTA, MET_CANTIDAD, MET_COSTO
    FECHA_AÑO, FECHA_MES

Uso:
    from schema_detector import detect_schema, detect_schema_from_raw

    # Desde registros ya enriquecidos (integración con el motor actual)
    profile = detect_schema(enriched_sales)

    # Desde headers + filas crudas (cualquier Excel sin mapping previo)
    profile = detect_schema_from_raw(headers, sample_rows)
"""

from collections import Counter
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Rol y sub-rol para cada campo semántico conocido del sistema actual
# ---------------------------------------------------------------------------

FIELD_ROLES = {
    # Fechas
    "date":              ("FECHA",          None),
    "year":              ("FECHA",          "FECHA_AÑO"),
    "month":             ("FECHA",          "FECHA_MES"),
    # Identificadores
    "client_key":        ("IDENTIFICADOR",  "ID_CLIENTE"),
    "invoice":           ("IDENTIFICADOR",  "ID_COMPROBANTE"),
    "product_key":       ("IDENTIFICADOR",  "ID_PRODUCTO"),
    "seller_key":        ("IDENTIFICADOR",  "ID_VENDEDOR"),
    # Dimensiones de cliente
    "client":            ("DIMENSIÓN",      "DIM_CLIENTE"),
    "client_name":       ("DIMENSIÓN",      "DIM_CLIENTE"),
    # Dimensiones de vendedor / territorio
    "seller_name":       ("DIMENSIÓN",      "DIM_VENDEDOR"),
    "sales_scheme_name": ("DIMENSIÓN",      "DIM_ESQUEMA_VENTAS"),
    "sales_force":       ("DIMENSIÓN",      "DIM_FUERZA"),
    "route_description": ("DIMENSIÓN",      "DIM_RUTA"),
    # Dimensiones de producto
    "product_name":      ("DIMENSIÓN",      "DIM_PRODUCTO"),
    "family":            ("DIMENSIÓN",      "DIM_FAMILIA"),
    "line":              ("DIMENSIÓN",      "DIM_LINEA"),
    "supplier":          ("DIMENSIÓN",      "DIM_PROVEEDOR"),
    "flavor":            ("DIMENSIÓN",      "DIM_SABOR"),
    "caliber":           ("DIMENSIÓN",      "DIM_CALIBRE"),
    "uxb":               ("DIMENSIÓN",      "DIM_UXB"),
    # Dimensiones de canal
    "channel":           ("DIMENSIÓN",      "DIM_CANAL"),
    # Métricas
    "amount":            ("MÉTRICA",        "MET_VENTA"),
    "quantity":          ("MÉTRICA",        "MET_CANTIDAD"),
}

# Valores centinela que indican "sin dato" (no se cuentan como cubiertos)
MISSING_SENTINELS = frozenset({
    "Sin vendedor", "Sin ruta", "Sin familia", "Sin canal",
    "Sin fuerza de ventas", "Sin proveedor", "Sin línea",
    "Sin sabor", "Sin UxB", "Sin calibre", "Sin producto", "Sin artículo",
})

# ---------------------------------------------------------------------------
# Reglas de análisis: qué roles/sub-roles se necesitan para habilitarlos
# ---------------------------------------------------------------------------

ANALYSIS_RULES = [
    {
        "id":             "temporal_trend",
        "label":          "Evolución temporal",
        "requires_roles": {"FECHA", "MÉTRICA"},
    },
    {
        "id":             "dimension_ranking",
        "label":          "Ranking y participación por dimensión",
        "requires_roles": {"DIMENSIÓN", "MÉTRICA"},
    },
    {
        "id":               "recurrence_churn",
        "label":            "Recurrencia y churn de cartera",
        "requires_subroles": {"DIM_CLIENTE", "MET_VENTA"},
        "requires_roles":   {"FECHA"},
    },
    {
        "id":               "cross_sell_mix",
        "label":            "Mix de producto por cliente",
        "requires_subroles": {"DIM_CLIENTE", "DIM_FAMILIA"},
    },
    {
        "id":               "seller_performance",
        "label":            "Performance de vendedores",
        "requires_subroles": {"DIM_VENDEDOR", "MET_VENTA"},
    },
    {
        "id":               "geographic_coverage",
        "label":            "Cobertura geográfica y rutas",
        "requires_subroles": {"DIM_RUTA", "MET_VENTA"},
    },
    {
        "id":               "margin_analysis",
        "label":            "Análisis de margen",
        "requires_subroles": {"MET_COSTO", "MET_VENTA"},
    },
    {
        "id":               "sales_force_breakdown",
        "label":            "Desglose por fuerza de ventas",
        "requires_subroles": {"DIM_FUERZA", "MET_VENTA"},
    },
    {
        "id":               "product_analysis",
        "label":            "Análisis de artículo / producto",
        "requires_subroles": {"DIM_PRODUCTO", "MET_VENTA"},
    },
    {
        "id":               "channel_analysis",
        "label":            "Análisis por canal",
        "requires_subroles": {"DIM_CANAL", "MET_VENTA"},
    },
]

# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def detect_schema(records):
    """
    Analiza una lista de dicts (registros ya normalizados y enriquecidos)
    y devuelve un SchemaProfile completo.

    Args:
        records: list[dict]  — registros post-enrich del motor actual.

    Returns:
        dict — SchemaProfile con campos, roles, sub-roles, análisis disponibles,
               rango de fechas, dimensiones y métricas detectadas.
    """
    if not records:
        return _empty_profile()

    total = len(records)

    all_keys: set = set()
    for record in records:
        all_keys.update(record.keys())

    field_stats = {}
    for field in sorted(all_keys):
        stats = _analyze_field(field, records, total)
        if stats is not None:
            field_stats[field] = stats

    return _build_profile(field_stats, total, records)


def detect_schema_from_raw(headers, sample_rows):
    """
    Detecta roles directamente desde headers y filas crudas (sin mapping previo).
    Útil para analizar cualquier Excel desconocido.

    Args:
        headers:     list[str]   — nombres de columna tal como aparecen en el Excel.
        sample_rows: list[list]  — filas de muestra (máx. 200 recomendado).

    Returns:
        dict — SchemaProfile con roles inferidos y análisis disponibles.
    """
    if not headers or not sample_rows:
        return _empty_profile()

    total = len(sample_rows)
    field_stats = {}

    for col_idx, header in enumerate(headers):
        col_values = [
            row[col_idx] if col_idx < len(row) else None
            for row in sample_rows
        ]
        stats = _analyze_raw_column(col_idx, header, col_values, total)
        if stats is not None:
            field_stats[header] = stats

    return _build_profile(field_stats, total, [])


# ---------------------------------------------------------------------------
# Núcleo: análisis de campos sobre registros normalizados
# ---------------------------------------------------------------------------

def _analyze_field(field, records, total):
    role, subrole = FIELD_ROLES.get(field, (None, None))

    values = []
    for record in records:
        v = record.get(field)
        if v is None:
            continue
        if isinstance(v, str) and (not v.strip() or v in MISSING_SENTINELS):
            continue
        values.append(v)

    if not values:
        return None  # campo vacío: excluir del perfil

    coverage = round(len(values) / total, 4)

    # Si el campo no está en FIELD_ROLES, inferir dinámicamente
    if role is None:
        role, subrole = _infer_role(field, values)

    cardinality = None
    top_values = None
    if role in ("DIMENSIÓN", "IDENTIFICADOR"):
        unique = {str(v) for v in values}
        cardinality = len(unique)
        top_values = [
            {"value": v, "count": c}
            for v, c in Counter(str(x) for x in values).most_common(5)
        ]

    return {
        "role":        role,
        "subrole":     subrole,
        "coverage":    coverage,
        "cardinality": cardinality,
        "top_values":  top_values,
    }


# ---------------------------------------------------------------------------
# Análisis de columnas crudas (sin mapping)
# ---------------------------------------------------------------------------

def _analyze_raw_column(col_idx, header, values, total):
    non_null = [v for v in values if v not in (None, "", False)]
    if not non_null:
        return None

    coverage = round(len(non_null) / max(total, 1), 4)

    # 1. Intentar detectar por nombre de columna
    role, subrole = _role_from_header(header)

    # 2. Si no hay match por nombre, inferir por contenido
    if role is None:
        role, subrole = _infer_role(header, non_null)

    cardinality = None
    top_values = None
    if role in ("DIMENSIÓN", "IDENTIFICADOR"):
        unique = {str(v) for v in non_null}
        cardinality = len(unique)
        top_values = [
            {"value": v, "count": c}
            for v, c in Counter(str(x) for x in non_null).most_common(5)
        ]

    return {
        "role":        role,
        "subrole":     subrole,
        "coverage":    coverage,
        "cardinality": cardinality,
        "top_values":  top_values,
        "col_index":   col_idx,
    }


# ---------------------------------------------------------------------------
# Inferencia de rol por contenido de valores
# ---------------------------------------------------------------------------

def _infer_role(name, values):
    """Infiere rol y sub-rol analizando el contenido de los valores."""
    sample = values[:200]
    n = max(len(sample), 1)

    # ¿Son principalmente values tipo date/datetime?
    date_hits = sum(1 for v in sample if isinstance(v, (date, datetime)))
    if date_hits / n >= 0.7:
        return "FECHA", None

    # ¿Son principalmente numéricos?
    numeric_hits = sum(1 for v in sample if isinstance(v, (int, float)))
    if numeric_hits / n >= 0.8:
        numeric_vals = [v for v in sample if isinstance(v, (int, float))]
        unique_nums = set(numeric_vals)
        # Cardinalidad de mes (1-12)
        if len(unique_nums) <= 12 and all(1 <= v <= 12 for v in unique_nums):
            return "FECHA", "FECHA_MES"
        # Cardinalidad de año (4 dígitos)
        if len(unique_nums) <= 30 and all(1900 <= v <= 2100 for v in unique_nums):
            return "FECHA", "FECHA_AÑO"
        return "MÉTRICA", _guess_metric_subrole(name)

    # Texto con cardinalidad baja → DIMENSIÓN
    str_vals = [str(v) for v in sample]
    unique_strs = set(str_vals)
    card_ratio = len(unique_strs) / max(len(str_vals), 1)
    if card_ratio > 0.8:
        return "IDENTIFICADOR", None

    return "DIMENSIÓN", _guess_dimension_subrole(name)


def _role_from_header(header):
    """Asigna rol desde el nombre del encabezado via keyword matching."""
    name = _normalize(header)

    checks = [
        (["fecha", "date", "periodo", "año", "mes", "year", "month", "dia", "día"],
         "FECHA", None),
        (["cliente", "customer", "cta", "cuenta", "razon social", "razón social"],
         "DIMENSIÓN", "DIM_CLIENTE"),
        (["vendedor", "asesor", "ejecutivo", "preventa", "rep ventas"],
         "DIMENSIÓN", "DIM_VENDEDOR"),
        (["articulo", "artículo", "producto", "sku", "item", "descripcion articulo"],
         "DIMENSIÓN", "DIM_PRODUCTO"),
        (["familia", "rubro", "categoria", "categoría"],
         "DIMENSIÓN", "DIM_FAMILIA"),
        (["ruta", "recorrido", "territorio"],
         "DIMENSIÓN", "DIM_RUTA"),
        (["canal", "segmento", "subcanal"],
         "DIMENSIÓN", "DIM_CANAL"),
        (["fuerza", "fza ventas", "fuerza ventas"],
         "DIMENSIÓN", "DIM_FUERZA"),
        (["linea", "línea"],
         "DIMENSIÓN", "DIM_LINEA"),
        (["proveedor", "supplier"],
         "DIMENSIÓN", "DIM_PROVEEDOR"),
        (["importe", "monto", "venta", "total", "neto", "facturado", "ingreso"],
         "MÉTRICA", "MET_VENTA"),
        (["costo", "cost", "compra"],
         "MÉTRICA", "MET_COSTO"),
        (["cantidad", "unidades", "qty", "cant"],
         "MÉTRICA", "MET_CANTIDAD"),
    ]

    for keywords, role, subrole in checks:
        if any(kw in name for kw in keywords):
            return role, subrole

    return None, None


def _guess_metric_subrole(name):
    name = name.lower()
    if any(kw in name for kw in ["costo", "cost", "compra"]):
        return "MET_COSTO"
    if any(kw in name for kw in ["cantidad", "unidades", "qty", "cant"]):
        return "MET_CANTIDAD"
    return "MET_VENTA"


def _guess_dimension_subrole(name):
    name = name.lower()
    if any(kw in name for kw in ["cliente", "customer", "cta"]):
        return "DIM_CLIENTE"
    if any(kw in name for kw in ["vendedor", "asesor", "seller"]):
        return "DIM_VENDEDOR"
    if any(kw in name for kw in ["familia", "rubro"]):
        return "DIM_FAMILIA"
    if any(kw in name for kw in ["ruta", "recorrido"]):
        return "DIM_RUTA"
    if any(kw in name for kw in ["canal", "channel"]):
        return "DIM_CANAL"
    if any(kw in name for kw in ["fuerza", "fza"]):
        return "DIM_FUERZA"
    if any(kw in name for kw in ["articulo", "artículo", "producto", "sku"]):
        return "DIM_PRODUCTO"
    return None


# ---------------------------------------------------------------------------
# Construcción del SchemaProfile
# ---------------------------------------------------------------------------

def _build_profile(field_stats, total, records):
    roles_index = _build_roles_index(field_stats)
    subroles_index = _build_subroles_index(field_stats)
    available_analyses = _resolve_analyses(roles_index, subroles_index)
    date_range = _date_range(records, field_stats)
    dimensions = _sorted_dimensions(field_stats)
    metrics = [f for f, s in field_stats.items() if s["role"] == "MÉTRICA"]

    return {
        "fields":             field_stats,
        "roles":              roles_index,
        "subroles":           subroles_index,
        "available_analyses": available_analyses,
        "record_count":       total,
        "date_range":         date_range,
        "dimensions":         dimensions,
        "metrics":            metrics,
    }


def _build_roles_index(field_stats):
    index = {"FECHA": [], "DIMENSIÓN": [], "MÉTRICA": [], "IDENTIFICADOR": []}
    for field, stats in field_stats.items():
        role = stats["role"]
        if role in index:
            index[role].append(field)
    return index


def _build_subroles_index(field_stats):
    """
    Mapea cada sub-rol al campo con mayor cobertura que lo tenga asignado.
    Si hay duplicados (ej: client y client_name ambos con DIM_CLIENTE),
    se queda con el de mayor cobertura.
    """
    candidates = {}
    for field, stats in field_stats.items():
        subrole = stats.get("subrole")
        if not subrole:
            continue
        existing = candidates.get(subrole)
        if existing is None or stats["coverage"] > field_stats[existing]["coverage"]:
            candidates[subrole] = field
    return candidates


def _resolve_analyses(roles_index, subroles_index):
    """Determina qué análisis están disponibles dado el perfil actual."""
    active_roles = {role for role, fields in roles_index.items() if fields}
    active_subroles = set(subroles_index.keys())
    # Añadir roles implícitos derivados de sub-roles
    effective_roles = active_roles | _subroles_to_roles(active_subroles)

    available = []
    for rule in ANALYSIS_RULES:
        req_roles = rule.get("requires_roles", set())
        req_subroles = rule.get("requires_subroles", set())

        if req_roles and not req_roles.issubset(effective_roles):
            continue
        if req_subroles and not req_subroles.issubset(active_subroles | effective_roles):
            continue

        available.append({"id": rule["id"], "label": rule["label"]})

    return available


def _subroles_to_roles(subroles):
    mapping = {
        "DIM_CLIENTE":   "DIMENSIÓN",
        "DIM_VENDEDOR":  "DIMENSIÓN",
        "DIM_FUERZA":    "DIMENSIÓN",
        "DIM_RUTA":      "DIMENSIÓN",
        "DIM_FAMILIA":   "DIMENSIÓN",
        "DIM_CANAL":     "DIMENSIÓN",
        "DIM_PRODUCTO":  "DIMENSIÓN",
        "DIM_LINEA":     "DIMENSIÓN",
        "DIM_PROVEEDOR": "DIMENSIÓN",
        "DIM_SABOR":     "DIMENSIÓN",
        "DIM_CALIBRE":   "DIMENSIÓN",
        "DIM_UXB":       "DIMENSIÓN",
        "MET_VENTA":     "MÉTRICA",
        "MET_CANTIDAD":  "MÉTRICA",
        "MET_COSTO":     "MÉTRICA",
        "FECHA_AÑO":     "FECHA",
        "FECHA_MES":     "FECHA",
        "ID_CLIENTE":    "IDENTIFICADOR",
        "ID_PRODUCTO":   "IDENTIFICADOR",
        "ID_VENDEDOR":   "IDENTIFICADOR",
        "ID_COMPROBANTE":"IDENTIFICADOR",
    }
    return {mapping[sr] for sr in subroles if sr in mapping}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _date_range(records, field_stats):
    """Calcula el rango de fechas desde el campo FECHA principal (sin sub-rol)."""
    date_fields = [
        f for f, s in field_stats.items()
        if s["role"] == "FECHA" and s.get("subrole") is None
    ]
    if not date_fields or not records:
        return None
    field = date_fields[0]
    dates = [
        r[field] for r in records
        if isinstance(r.get(field), (date, datetime))
    ]
    if not dates:
        return None
    min_d, max_d = min(dates), max(dates)
    return {
        "min": min_d.isoformat() if hasattr(min_d, "isoformat") else str(min_d),
        "max": max_d.isoformat() if hasattr(max_d, "isoformat") else str(max_d),
    }


def _sorted_dimensions(field_stats):
    """
    Retorna las dimensiones ordenadas por:
    1. Cobertura descendente (más datos primero)
    2. Cardinalidad ascendente (más específicas primero)
    """
    dims = [
        (field, stats["coverage"], stats.get("cardinality") or 0)
        for field, stats in field_stats.items()
        if stats["role"] == "DIMENSIÓN"
    ]
    dims.sort(key=lambda x: (-x[1], x[2]))
    return [d[0] for d in dims]


def _normalize(text):
    return str(text).strip().lower() if text is not None else ""


def _empty_profile():
    return {
        "fields":             {},
        "roles":              {"FECHA": [], "DIMENSIÓN": [], "MÉTRICA": [], "IDENTIFICADOR": []},
        "subroles":           {},
        "available_analyses": [],
        "record_count":       0,
        "date_range":         None,
        "dimensions":         [],
        "metrics":            [],
    }
