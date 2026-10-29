#!/usr/bin/env python3
"""Offline by default; explicit, bounded catalog inspection on the ODBC host."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sales_coach.odbc_inspection import (  # noqa: E402
    InspectionScope, OdbcConfig, OdbcInspectionError, inspect_schema,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preparación ODBC sin conexión; --inspect-schema consulta sólo metadatos.")
    parser.add_argument("--inspect-schema", action="store_true")
    parser.add_argument("--schema", help="Esquema exacto confirmado por el administrador de Chess")
    parser.add_argument("--table-pattern", help="Nombre o prefijo de tablas/vistas autorizado; admite %% y _")
    parser.add_argument("--max-tables", type=int, default=5)
    parser.add_argument("--max-columns", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        if not args.inspect_schema:
            result = {
                "mode": "offline", "connection_attempted": False,
                "required_environment": ["CHESS_ODBC_USER", "CHESS_ODBC_PASSWORD"],
                "optional_environment": {"CHESS_ODBC_DSN": "CHESS_CODENOA"},
                "next_step": "Verificar permisos de lectura y esquema/prefijo autorizado en PC-Techstation; usar --inspect-schema allí.",
                "production_sync_enabled": False,
            }
        else:
            if not args.schema or not args.table_pattern:
                raise OdbcInspectionError("La inspección requiere --schema y --table-pattern explícitos.")
            scope = InspectionScope(args.schema, args.table_pattern, args.max_tables, args.max_columns)
            result = inspect_schema(OdbcConfig.from_env(), scope)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") in {"truncated", "no_matches"} else 0
    except OdbcInspectionError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
