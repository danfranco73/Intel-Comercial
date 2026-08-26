from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

DEFAULT_PORT = 5432
DEFAULT_DATABASE = "postgres"
DEFAULT_SSLMODE = "require"
DEFAULT_CONNECT_TIMEOUT = 10.0


class SupabaseError(RuntimeError):
    pass


def get_supabase_config():
    return {
        "host": os.getenv("SUPABASE_PG_HOST") or "",
        "port": _parse_int_env("SUPABASE_PG_PORT", DEFAULT_PORT),
        "database": os.getenv("SUPABASE_PG_DATABASE") or DEFAULT_DATABASE,
        "user": os.getenv("SUPABASE_PG_USER") or "",
        "password": os.getenv("SUPABASE_PG_PASSWORD") or "",
        "sslmode": os.getenv("SUPABASE_PG_SSLMODE") or DEFAULT_SSLMODE,
        "connect_timeout": _parse_float_env("SUPABASE_PG_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT),
    }


def supabase_configured():
    config = get_supabase_config()
    return bool(config["host"] and config["user"] and config["password"])


def supabase_sync_enabled():
    value = (os.getenv("SUPABASE_SYNC_ENABLED") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_supabase_status():
    if not supabase_configured():
        return {
            "configured": False,
            "reachable": False,
            "message": "Faltan variables de entorno para Supabase (gestion_reader).",
        }
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {
            "configured": True,
            "reachable": True,
            "syncEnabled": supabase_sync_enabled(),
            "message": "Supabase (TMA) autenticado correctamente con gestion_reader.",
        }
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "syncEnabled": supabase_sync_enabled(),
            "message": f"No se pudo conectar a Supabase: {exc}",
        }


def _connect():
    import psycopg

    if not supabase_configured():
        raise SupabaseError("Supabase (gestion_reader) no está configurado en .env")
    config = get_supabase_config()
    return psycopg.connect(
        host=config["host"],
        port=config["port"],
        dbname=config["database"],
        user=config["user"],
        password=config["password"],
        sslmode=config["sslmode"],
        connect_timeout=config["connect_timeout"],
        autocommit=True,
    )


def fetch_products_dataset(conn=None):
    """Trae atributos comerciales (precio, stock, listas de precio) sincronizados
    por TMA desde ChessERP. NO reemplaza el maestro de artículos (`erp_articles`,
    que sigue viniendo de ChessERP directo) — este es un dataset paralelo, con
    datasetType propio, deliberadamente distinto de "articles" para que nunca
    pueda engancharse por accidente en un sitio que espera artículos de ChessERP.
    """
    owns_conn = conn is None
    conn = conn or _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.erp_id, p.title, p.price, p.internal_tax, p.stock,
                       p.unit, p.units_per_pack, p.category, p.is_active,
                       p.supplier, p.brand, p.calibre, p.product_segment,
                       pf.name AS family_name
                FROM products p
                LEFT JOIN product_families pf ON pf.id = p.family_id
                """
            )
            columns = [desc[0] for desc in cur.description]
            product_rows = [dict(zip(columns, row)) for row in cur.fetchall()]

            cur.execute(
                "SELECT product_id, price_list, price, internal_tax FROM product_prices"
            )
            price_columns = [desc[0] for desc in cur.description]
            price_rows = [dict(zip(price_columns, row)) for row in cur.fetchall()]
    finally:
        if owns_conn:
            conn.close()

    price_lists_by_product: dict = {}
    for row in price_rows:
        price_lists_by_product.setdefault(row["product_id"], []).append(
            {
                "price_list": row.get("price_list"),
                "price": _to_float(row.get("price")),
                "internal_tax": _to_float(row.get("internal_tax")),
            }
        )

    records = []
    for row in product_rows:
        record = normalize_supabase_product_row(row, price_lists_by_product.get(row["id"], []))
        if record:
            records.append(record)

    return {
        "datasetType": "products_commercial",
        "sourceKind": "supabase",
        "file": "Supabase (TMA) productos",
        "sheet": "products",
        "headerRow": 0,
        "rowsRead": len(product_rows),
        "rowsValid": len(records),
        "headers": [],
        "mapping": {},
        "records": records,
    }


def fetch_clients_dataset(conn=None):
    """Trae el maestro de clientes que TMA sincroniza desde ChessERP hacia
    `profiles`. GESTION no tiene hoy ningún maestro de clientes propio — este
    dataset es una capacidad nueva, no un reemplazo de nada existente.
    """
    owns_conn = conn is None
    conn = conn or _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT client_code, company, cuit, tax_condition, price_list,
                       assigned_seller_code, marketing_subchannel_code,
                       marketing_subchannel_description, credit_limit,
                       unpaid_vouchers, overdue_debt_days, payment_method_code,
                       payment_method_description, payment_term_days,
                       created_at, updated_at
                FROM profiles
                WHERE client_code IS NOT NULL
                """
            )
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        if owns_conn:
            conn.close()

    records = []
    for row in rows:
        record = normalize_supabase_client_row(row)
        if record:
            records.append(record)

    return {
        "datasetType": "clients",
        "sourceKind": "supabase",
        "file": "Supabase (TMA) clientes",
        "sheet": "profiles",
        "headerRow": 0,
        "rowsRead": len(rows),
        "rowsValid": len(records),
        "headers": [],
        "mapping": {},
        "records": records,
    }


def get_supabase_sync_freshness(conn=None):
    """Última corrida conocida por modo (full/stock/prices/...) según `sync_logs`,
    para poder advertir si el dato de TMA está desactualizado antes de usarlo.
    """
    owns_conn = conn is None
    conn = conn or _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (mode) mode, status, started_at, finished_at, stats
                FROM sync_logs
                ORDER BY mode, started_at DESC
                """
            )
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        if owns_conn:
            conn.close()
    return rows


def normalize_supabase_product_row(row, price_lists):
    product_key = _standard_key(row.get("erp_id"))
    if not product_key:
        return None
    return {
        "product_key": product_key,
        "product_name": _clean_text(row.get("title")),
        "price": _to_float(row.get("price")),
        "internal_tax": _to_float(row.get("internal_tax")),
        "stock": _to_float(row.get("stock")),
        "unit": _clean_text(row.get("unit")),
        "units_per_pack": _to_float(row.get("units_per_pack")),
        "category": _clean_text(row.get("category")),
        "family": _clean_text(row.get("family_name")),
        "brand": _clean_text(row.get("brand")),
        "supplier": _clean_text(row.get("supplier")),
        "caliber": _clean_text(row.get("calibre")),
        "segment": _clean_text(row.get("product_segment")),
        "is_active": bool(row.get("is_active")),
        "price_lists": price_lists,
        "source": "Supabase",
    }


def normalize_supabase_client_row(row):
    client_key = _standard_key(row.get("client_code"))
    if not client_key:
        return None
    return {
        "client_key": client_key,
        "company": _clean_text(row.get("company")),
        "cuit": _clean_text(row.get("cuit")),
        "tax_condition": _clean_text(row.get("tax_condition")),
        "price_list": _clean_text(row.get("price_list")),
        "assigned_seller_key": _standard_key(row.get("assigned_seller_code")),
        "marketing_subchannel_key": _clean_text(row.get("marketing_subchannel_code")),
        "marketing_subchannel_name": _clean_text(row.get("marketing_subchannel_description")),
        "credit_limit": _to_float(row.get("credit_limit")),
        "unpaid_vouchers": _to_float(row.get("unpaid_vouchers")),
        "overdue_debt_days": _to_float(row.get("overdue_debt_days")),
        "payment_method_key": _clean_text(row.get("payment_method_code")),
        "payment_method_name": _clean_text(row.get("payment_method_description")),
        "payment_term_days": _to_float(row.get("payment_term_days")),
        "source": "Supabase",
    }


def _clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _standard_key(value):
    text = _clean_text(value)
    if not text or text == "0":
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return text


def _parse_int_env(name, default_value):
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default_value
    try:
        return int(str(value).strip())
    except ValueError:
        return default_value


def _parse_float_env(name, default_value):
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default_value
    try:
        return float(str(value).strip())
    except ValueError:
        return default_value
