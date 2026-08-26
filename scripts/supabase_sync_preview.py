#!/usr/bin/env python3
"""Diagnóstico de solo lectura: no escribe nada en Mongo ni en Supabase.

Se conecta a Supabase (TMA) con el rol de solo lectura `gestion_reader`,
imprime conteos/muestras de `products`/`profiles`, y compara `product_key`/
`client_key` contra lo que ya hay en `erp_articles`/`erp_sales` en Mongo para
cuantificar la tasa de match ANTES de sincronizar nada.

Uso:
    .venv/bin/python scripts/supabase_sync_preview.py
"""

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.chdir(ROOT_DIR)

from supabase_client import (  # noqa: E402
    fetch_clients_dataset,
    fetch_products_dataset,
    get_supabase_config,
    get_supabase_status,
    get_supabase_sync_freshness,
    supabase_configured,
)
import mongo_client  # noqa: E402


def _sample(records, n=5):
    return records[:n]


def main():
    print("== Estado de conexión a Supabase (gestion_reader) ==")
    if not supabase_configured():
        print("Faltan SUPABASE_PG_HOST / SUPABASE_PG_USER / SUPABASE_PG_PASSWORD en .env.")
        print("Completá esas variables (agregadas ya con placeholders) y volvé a correr este script.")
        return 1

    config = get_supabase_config()
    print(f"Host: {config['host']}:{config['port']} · DB: {config['database']} · user: {config['user']}")

    status = get_supabase_status()
    print(json.dumps(status, indent=2, ensure_ascii=False))
    if not status.get("reachable"):
        return 1

    print("\n== Frescura de sync en TMA (sync_logs) ==")
    try:
        freshness = get_supabase_sync_freshness()
        for row in freshness:
            print(f"  {row.get('mode'):>14} · {row.get('status'):>8} · terminado: {row.get('finished_at')}")
    except Exception as exc:
        print(f"  No se pudo leer sync_logs: {exc}")

    print("\n== products (Supabase) ==")
    products = fetch_products_dataset()
    print(f"rowsRead={products['rowsRead']} rowsValid={products['rowsValid']}")
    for record in _sample(products["records"]):
        print(" ", {k: record[k] for k in ("product_key", "product_name", "price", "stock") if k in record})

    print("\n== profiles / clientes (Supabase) ==")
    clients = fetch_clients_dataset()
    print(f"rowsRead={clients['rowsRead']} rowsValid={clients['rowsValid']}")
    for record in _sample(clients["records"]):
        print(" ", {k: record[k] for k in ("client_key", "company", "credit_limit", "overdue_debt_days") if k in record})

    print("\n== Tasa de match contra Mongo (erp_articles / erp_sales) ==")
    supabase_product_keys = {r["product_key"] for r in products["records"]}
    supabase_client_keys = {r["client_key"] for r in clients["records"]}

    try:
        mongo_articles = mongo_client.load_erp_articles_dataset()
        mongo_product_keys = {r.get("product_key") for r in mongo_articles["records"] if r.get("product_key")}
        matched_products = supabase_product_keys & mongo_product_keys
        print(
            f"Productos: {len(supabase_product_keys)} en Supabase, {len(mongo_product_keys)} en Mongo "
            f"(erp_articles), {len(matched_products)} matchean por product_key "
            f"({100 * len(matched_products) / max(1, len(supabase_product_keys)):.1f}% de Supabase)."
        )
        no_match_sample = list(supabase_product_keys - mongo_product_keys)[:10]
        if no_match_sample:
            print(f"  Ejemplos de product_key en Supabase sin match en Mongo: {no_match_sample}")
    except Exception as exc:
        print(f"No se pudo comparar contra erp_articles: {exc}")

    try:
        db = mongo_client.get_db()
        mongo_client_keys = set(db[mongo_client.ERP_SALES_COLLECTION].distinct("client_key")) if db is not None else set()
        matched_clients = supabase_client_keys & mongo_client_keys
        print(
            f"Clientes: {len(supabase_client_keys)} en Supabase, {len(mongo_client_keys)} client_key distintos "
            f"en erp_sales, {len(matched_clients)} matchean "
            f"({100 * len(matched_clients) / max(1, len(supabase_client_keys)):.1f}% de Supabase)."
        )
    except Exception as exc:
        print(f"No se pudo comparar contra erp_sales: {exc}")

    print("\nNada de esto se escribió en Mongo. Este script es puramente de diagnóstico.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
