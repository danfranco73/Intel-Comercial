from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = "America/Argentina/Cordoba"
DIMENSIONS = {
    "company": ("company_key", "company_name"),
    "structure": ("sales_scheme_key", "sales_scheme_name"),
    "sales_force": ("sales_scheme_key", "sales_scheme_name"),
    "business_unit": ("business_unit", "business_unit"),
    "channel": ("channel", "channel"), "seller": ("seller_key", "seller_name"),
    "route": ("route_description", "route_description"),
    "supplier": ("supplier", "supplier"), "family": ("family", "family"),
    "line": ("line", "line"), "product": ("product_key", "product_name"),
    "client": ("client_key", "client_name"),
}
HIERARCHY = ["structure", "channel", "seller", "supplier", "family", "line", "product", "client"]


def sales_request(query):
    allowed = set(DIMENSIONS) | {"as_of", "dimension", "limit", "offset", "context_id"}
    if set(query) - allowed:
        raise ValueError("Parámetro desconocido")
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    end = date.fromisoformat(query.get("as_of") or (today - timedelta(days=1)).isoformat())
    if end > today:
        raise ValueError("as_of no puede estar en el futuro")
    dimension = query.get("dimension", "structure")
    if dimension not in DIMENSIONS:
        raise ValueError("Dimensión inválida")
    limit, offset = page(query)
    filters = {key: str(query[key]).strip() for key in DIMENSIONS if query.get(key) is not None}
    if any(not value for value in filters.values()):
        raise ValueError("Filtro vacío")
    if "structure" in filters and "sales_force" in filters and filters["structure"] != filters["sales_force"]:
        raise ValueError("Estructura y fuerza representan el mismo esquema")
    return {"as_of": end, "filters": filters, "dimension": dimension,
            "limit": limit, "offset": offset, "context_id": query.get("context_id")}


def page(query):
    limit, offset = int(query.get("limit", 50)), int(query.get("offset", 0))
    if not 1 <= limit <= 200 or not 0 <= offset <= 1000000:
        raise ValueError("Paginación fuera de rango")
    return limit, offset
