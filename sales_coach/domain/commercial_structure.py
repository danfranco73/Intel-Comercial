from __future__ import annotations

"""Estructura comercial de Codenoa: mapeo de fuerzas de venta (esquemas) a los
bloques de negocio que Dirección Comercial usa para fijar objetivos.

Fuente: `idFuerzaVentas` en Chess, persistido como `sales_scheme_key` /
`sales_force_key` en `erp_sales` y `erp_sellers` (ver `erp_client.py`). El ERP
reparte cada pedido combinado -incluidos los de la app B2B TeMando- en su
esquema real antes de llegar a `erp_sales`, así que cada línea de venta
pertenece a exactamente un bloque: no hay solapamiento entre ellos.

`B2B` (esquema 5) son ventas de clientes sin cobertura de preventa que
ingresan por la app; un cliente visitado que además compra por la app cae en
su esquema habitual (1-4), no en B2B. Mapeo confirmado con Dirección
Comercial el 2026-09-03; no debe modificarse sin ese visto bueno.
"""


COMMERCIAL_STRUCTURE_SCHEMES: tuple[tuple[str, str], ...] = (
    ("1", "Bebidas"),
    ("2", "Mercadería"),
    ("3", "Frescos"),
    ("4", "Mayorista"),
    ("5", "B2B"),
)

VALID_COMMERCIAL_STRUCTURE_KEYS = frozenset(key for key, _ in COMMERCIAL_STRUCTURE_SCHEMES)

_LABEL_BY_KEY: dict[str, str] = dict(COMMERCIAL_STRUCTURE_SCHEMES)


def _normalize(text: str) -> str:
    value = str(text or "").strip().lower()
    for src, target in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        value = value.replace(src, target)
    return value


_KEY_BY_NORMALIZED_LABEL: dict[str, str] = {
    _normalize(label): key for key, label in COMMERCIAL_STRUCTURE_SCHEMES
}


def commercial_structure_label(scope_key: str) -> str:
    return _LABEL_BY_KEY.get(str(scope_key).strip(), str(scope_key))


def resolve_commercial_structure_key(raw: str) -> str | None:
    """Acepta la clave del esquema (``"1"``) o su etiqueta (``"Bebidas"``,
    sin distinguir mayúsculas ni acentos) y devuelve la clave canónica, o
    ``None`` si no coincide con ningún esquema conocido."""
    text = str(raw or "").strip()
    if text in VALID_COMMERCIAL_STRUCTURE_KEYS:
        return text
    return _KEY_BY_NORMALIZED_LABEL.get(_normalize(text))


def ordered_commercial_structure_keys() -> list[str]:
    return [key for key, _ in COMMERCIAL_STRUCTURE_SCHEMES]
