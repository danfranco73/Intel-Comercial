#!/usr/bin/env python3

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.chdir(ROOT_DIR)

from app import _erp_masters_available, _parse_iso_date, _sync_sales_range_chunked  # noqa: E402
from clickhouse_client import get_clickhouse_storage_status  # noqa: E402
from erp_client import (  # noqa: E402
    erp_login,
    fetch_articles_dataset,
    fetch_marketing_dataset,
    fetch_routes_dataset,
    fetch_staff_dataset,
)
from mongo_client import (  # noqa: E402
    get_erp_storage_status,
    sync_erp_articles,
    sync_erp_marketing,
    sync_erp_routes,
    sync_erp_sellers,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sincroniza ventas ChessERP directo desde código, sin depender del front.",
    )
    parser.add_argument("--from-date", required=True, help="Fecha inicial en formato YYYY-MM-DD.")
    parser.add_argument("--to-date", required=True, help="Fecha final en formato YYYY-MM-DD.")
    parser.add_argument(
        "--force-refresh-sales",
        action="store_true",
        help="Vuelve a consultar el rango aunque ya esté cubierto en la base.",
    )
    parser.add_argument(
        "--refresh-masters",
        action="store_true",
        help="Refresca además artículos, vendedores, rutas y marketing.",
    )
    parser.add_argument(
        "--allow-future-end",
        action="store_true",
        help="Permite pedir una fecha final futura. Por defecto se recorta a hoy.",
    )
    return parser


def _sync_masters(cookie: str) -> dict:
    articles = fetch_articles_dataset(cookie=cookie)
    sellers = fetch_staff_dataset(cookie=cookie)
    routes = fetch_routes_dataset(cookie=cookie)
    marketing = fetch_marketing_dataset(cookie=cookie)
    return {
        "articlesSync": sync_erp_articles(articles["records"], origin="cli_sync"),
        "sellersSync": sync_erp_sellers(sellers["records"], origin="cli_sync"),
        "routesSync": sync_erp_routes(routes["records"], origin="cli_sync"),
        "marketingSync": sync_erp_marketing(marketing["records"], origin="cli_sync"),
        "articlesRowsValid": articles.get("rowsValid", 0),
        "sellersRowsValid": sellers.get("rowsValid", 0),
        "routesRowsValid": routes.get("rowsValid", 0),
        "marketingRowsValid": marketing.get("rowsValid", 0),
    }


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    start = _parse_iso_date(args.from_date, "from-date")
    end = _parse_iso_date(args.to_date, "to-date")
    today = date.today()

    if not args.allow_future_end and end > today:
        print(
            f"[info] La fecha final solicitada {end.isoformat()} es futura para esta ejecución. "
            f"Se ajusta automáticamente a {today.isoformat()}.",
            file=sys.stderr,
        )
        end = today

    if start > end:
        parser.error("from-date no puede ser mayor que to-date")

    print(
        f"[info] Iniciando sync ERP desde código para {start.isoformat()} a {end.isoformat()} "
        f"(force_refresh_sales={args.force_refresh_sales}, refresh_masters={args.refresh_masters})"
    )

    session = erp_login()
    cookie = session.get("cookie")
    if not cookie:
        raise RuntimeError("No se pudo obtener cookie válida de ChessERP")

    sales_summary = _sync_sales_range_chunked(
        start.isoformat(),
        end.isoformat(),
        cookie=cookie,
        force_refresh=args.force_refresh_sales,
    )

    storage = get_erp_storage_status()
    masters_summary = None
    should_sync_masters = args.refresh_masters or not _erp_masters_available(storage)
    if should_sync_masters:
        masters_summary = _sync_masters(cookie)
        storage = get_erp_storage_status()

    payload = {
        "range": {"fromDate": start.isoformat(), "toDate": end.isoformat()},
        "salesSync": sales_summary,
        "mastersSynced": should_sync_masters,
        "masters": masters_summary,
        "mongoStorage": storage,
        "clickhouseStorage": get_clickhouse_storage_status(),
    }

    print(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
