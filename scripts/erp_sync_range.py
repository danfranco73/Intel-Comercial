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
from sales_coach.services.sync_service import SyncService  # noqa: E402


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

    result = SyncService(
        _sync_sales_range_chunked,
        _erp_masters_available,
    ).run(
        {
            "fechaDesde": start.isoformat(),
            "fechaHasta": end.isoformat(),
            "forceRefreshSales": args.force_refresh_sales,
            "refreshMasters": args.refresh_masters,
        },
        requested_by="cli",
        origin="cli",
    )

    payload = {
        "range": {"fromDate": start.isoformat(), "toDate": end.isoformat()},
        "runId": result["runId"],
        "salesSync": result["sync"],
        "mastersSynced": result["mastersSynced"],
        "masters": {
            "articlesSync": result["articlesSync"],
            "sellersSync": result["sellersSync"],
            "routesSync": result["routesSync"],
            "marketingSync": result["marketingSync"],
        } if result["mastersSynced"] else None,
        "mongoStorage": result["storage"],
        "clickhouseStorage": result["clickhouseStorage"],
        "reconciliation": result["reconciliation"],
    }

    print(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
