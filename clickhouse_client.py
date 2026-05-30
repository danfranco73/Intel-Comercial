from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env")

try:
    import clickhouse_connect
except ImportError:  # pragma: no cover - dependencia opcional
    clickhouse_connect = None


CLICKHOUSE_DEFAULT_PORT = 8443
CLICKHOUSE_DEFAULT_DATABASE = "gestion_comercial"
CLICKHOUSE_BOOTSTRAP_DATABASE = "default"
CLICKHOUSE_DEFAULT_TABLE = "fact_sales_compact"
CLICKHOUSE_DEFAULT_TIMEOUT = 15
CLICKHOUSE_DEFAULT_MUTATION_TIMEOUT = 180
CLICKHOUSE_SALES_COLUMNS = [
    "date",
    "year",
    "month",
    "client_key",
    "client_name",
    "route_description",
    "seller_key",
    "seller_name",
    "sales_scheme_key",
    "sales_scheme_name",
    "sales_force",
    "product_key",
    "invoice",
    "channel",
    "amount",
    "amount_net",
    "amount_final",
    "internal_taxes",
    "amount_net_internal",
    "quantity",
    "sync_run_id",
    "origin",
    "synced_at",
]

_client = None
_schema_ready = False


def _config():
    raw_host = (os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST") or "").strip()
    parsed = urlparse(raw_host) if raw_host and "://" in raw_host else None
    normalized_host = parsed.hostname if parsed and parsed.hostname else raw_host
    env_port = os.getenv("CLICKHOUSE_PORT")
    resolved_port = int(env_port) if env_port else (parsed.port if parsed and parsed.port else CLICKHOUSE_DEFAULT_PORT)
    secure_env = os.getenv("CLICKHOUSE_SECURE")
    secure_default = parsed.scheme.lower() == "https" if parsed and parsed.scheme else True
    return {
        "host": normalized_host,
        "port": resolved_port,
        "username": (os.getenv("CLICKHOUSE_USERNAME") or os.getenv("CH_USER") or "default").strip(),
        "password": os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD") or "",
        "database": (os.getenv("CLICKHOUSE_DATABASE") or CLICKHOUSE_DEFAULT_DATABASE).strip(),
        "bootstrap_database": (os.getenv("CLICKHOUSE_BOOTSTRAP_DATABASE") or CLICKHOUSE_BOOTSTRAP_DATABASE).strip(),
        "table": (os.getenv("CLICKHOUSE_SALES_TABLE") or CLICKHOUSE_DEFAULT_TABLE).strip(),
        "secure": (secure_env or str(secure_default)).strip().lower() not in {"0", "false", "no"},
        "timeout": int(os.getenv("CLICKHOUSE_TIMEOUT") or CLICKHOUSE_DEFAULT_TIMEOUT),
        "mutation_timeout": int(os.getenv("CLICKHOUSE_MUTATION_TIMEOUT") or CLICKHOUSE_DEFAULT_MUTATION_TIMEOUT),
    }


def clickhouse_configured():
    cfg = _config()
    return bool(clickhouse_connect and cfg["host"] and cfg["database"] and cfg["table"])


def get_clickhouse_client():
    global _client
    if not clickhouse_configured():
        return None
    if _client is None:
        cfg = _config()
        _client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["port"],
            username=cfg["username"],
            password=cfg["password"],
            database=cfg["bootstrap_database"] or CLICKHOUSE_BOOTSTRAP_DATABASE,
            secure=cfg["secure"],
            connect_timeout=cfg["timeout"],
            send_receive_timeout=cfg["timeout"],
        )
    return _client


def _qualified_table():
    cfg = _config()
    return f"{cfg['database']}.{cfg['table']}"


def _ensure_schema():
    global _schema_ready
    client = get_clickhouse_client()
    if client is None or _schema_ready:
        return
    cfg = _config()
    client.command(f"CREATE DATABASE IF NOT EXISTS {cfg['database']}")
    client.command(
        f"""
        CREATE TABLE IF NOT EXISTS {_qualified_table()} (
            date Date,
            year UInt16,
            month UInt8,
            client_key String,
            client_name String,
            route_description String,
            seller_key String,
            seller_name String,
            sales_scheme_key String,
            sales_scheme_name String,
            sales_force String,
            product_key String,
            invoice String,
            channel String,
            amount Float64,
            amount_net Float64,
            amount_final Float64,
            internal_taxes Float64,
            amount_net_internal Float64,
            quantity Float64,
            sync_run_id String,
            origin LowCardinality(String),
            synced_at DateTime
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(date)
        ORDER BY (date, client_key, seller_key, product_key, invoice)
        SETTINGS index_granularity = 8192
        """
    )
    client.command(
        f"ALTER TABLE {_qualified_table()} ADD COLUMN IF NOT EXISTS sync_run_id String DEFAULT ''"
    )
    for column in ("seller_name", "sales_scheme_key", "sales_scheme_name", "sales_force"):
        client.command(f"ALTER TABLE {_qualified_table()} ADD COLUMN IF NOT EXISTS {column} String DEFAULT ''")
    for column in ("amount_net", "amount_final", "internal_taxes", "amount_net_internal"):
        client.command(
            f"ALTER TABLE {_qualified_table()} ADD COLUMN IF NOT EXISTS {column} Float64 DEFAULT amount"
            if column != "internal_taxes"
            else f"ALTER TABLE {_qualified_table()} ADD COLUMN IF NOT EXISTS {column} Float64 DEFAULT 0"
        )
    _schema_ready = True


def _empty_sales_confirmed(warning):
    text = str(warning or "").strip().lower()
    return "no devolvió ventas" in text or "no generó lotes" in text


def _compact_records(records):
    grouped = {}
    for record in records:
        sale_date = record.get("date")
        if isinstance(sale_date, str):
            sale_date = date.fromisoformat(sale_date)
        if not isinstance(sale_date, date):
            continue
        key = (
            sale_date.isoformat(),
            str(record.get("client_key") or ""),
            str(record.get("invoice") or record.get("document_key") or ""),
            str(record.get("product_key") or ""),
            str(record.get("seller_key") or ""),
            str(record.get("sales_scheme_key") or ""),
            str(record.get("route_description") or ""),
            str(record.get("channel") or ""),
        )
        current = grouped.get(key)
        if current is None:
            current = {
                "date": sale_date,
                "year": int(record.get("year") or sale_date.year),
                "month": int(record.get("month") or sale_date.month),
                "client_key": str(record.get("client_key") or ""),
                "client_name": str(record.get("client_name") or record.get("client_key") or ""),
                "route_description": str(record.get("route_description") or ""),
                "seller_key": str(record.get("seller_key") or ""),
                "seller_name": str(record.get("seller_name") or ""),
                "sales_scheme_key": str(record.get("sales_scheme_key") or ""),
                "sales_scheme_name": str(record.get("sales_scheme_name") or record.get("sales_force") or ""),
                "sales_force": str(record.get("sales_force") or record.get("sales_scheme_name") or ""),
                "product_key": str(record.get("product_key") or ""),
                "invoice": str(record.get("invoice") or record.get("document_key") or ""),
                "channel": str(record.get("channel") or ""),
                "amount": float(record.get("amount") or 0),
                "amount_net": float(record.get("amount_net") if record.get("amount_net") is not None else record.get("amount") or 0),
                "amount_final": float(record.get("amount_final") if record.get("amount_final") is not None else record.get("amount") or 0),
                "internal_taxes": float(record.get("internal_taxes") or 0),
                "amount_net_internal": float(record.get("amount_net_internal") if record.get("amount_net_internal") is not None else (record.get("amount_net") if record.get("amount_net") is not None else record.get("amount") or 0) + (record.get("internal_taxes") or 0)),
                "quantity": float(record.get("quantity") or 0),
            }
            grouped[key] = current
            continue
        current["amount"] = round(current["amount"] + float(record.get("amount") or 0), 6)
        current["amount_net"] = round(current["amount_net"] + float(record.get("amount_net") if record.get("amount_net") is not None else record.get("amount") or 0), 6)
        current["amount_final"] = round(current["amount_final"] + float(record.get("amount_final") if record.get("amount_final") is not None else record.get("amount") or 0), 6)
        current["internal_taxes"] = round(current["internal_taxes"] + float(record.get("internal_taxes") or 0), 6)
        current["amount_net_internal"] = round(current["amount_net_internal"] + float(record.get("amount_net_internal") if record.get("amount_net_internal") is not None else (record.get("amount_net") if record.get("amount_net") is not None else record.get("amount") or 0) + (record.get("internal_taxes") or 0)), 6)
        current["quantity"] = round(current["quantity"] + float(record.get("quantity") or 0), 6)
        if not current["client_name"]:
            current["client_name"] = str(record.get("client_name") or record.get("client_key") or "")
    return list(grouped.values())


def _wait_for_mutations(client, timeout_seconds):
    cfg = _config()
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        pending = client.query(
            f"""
            SELECT count()
            FROM system.mutations
            WHERE database = '{cfg["database"]}'
              AND table = '{cfg["table"]}'
              AND is_done = 0
            """
        ).result_rows[0][0]
        if not pending:
            return
        time.sleep(1.0)
    raise TimeoutError("ClickHouse no terminó las mutaciones a tiempo")


def sync_erp_sales_clickhouse(records, fecha_desde, fecha_hasta, origin="manual", rows_read=0, warning=None):
    client = get_clickhouse_client()
    if client is None:
        return {
            "configured": False,
            "available": False,
            "message": "ClickHouse no está configurado.",
        }
    _ensure_schema()
    compact_records = _compact_records(records)
    confirmed_empty = _empty_sales_confirmed(warning)
    if not compact_records and not confirmed_empty:
        raise RuntimeError(
            f"Sync ClickHouse abortada para {fecha_desde} a {fecha_hasta}: ChessERP devolvió 0 filas válidas."
        )

    table = _qualified_table()
    sync_run_id = uuid4().hex
    inserted = 0
    if compact_records:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [
            [
                item["date"],
                int(item["year"]),
                int(item["month"]),
                item["client_key"],
                item["client_name"],
                item["route_description"],
                item["seller_key"],
                item["seller_name"],
                item["sales_scheme_key"],
                item["sales_scheme_name"],
                item["sales_force"],
                item["product_key"],
                item["invoice"],
                item["channel"],
                float(item["amount"]),
                float(item["amount_net"]),
                float(item["amount_final"]),
                float(item["internal_taxes"]),
                float(item["amount_net_internal"]),
                float(item["quantity"]),
                sync_run_id,
                origin,
                now,
            ]
            for item in compact_records
        ]
        client.insert(table=table, data=rows, column_names=CLICKHOUSE_SALES_COLUMNS)
        inserted = len(rows)

    delete_filter = (
        f"""
        ALTER TABLE {table}
        DELETE WHERE date >= toDate('{fecha_desde}')
          AND date <= toDate('{fecha_hasta}')
          AND sync_run_id != '{sync_run_id}'
        """
        if compact_records
        else f"""
        ALTER TABLE {table}
        DELETE WHERE date >= toDate('{fecha_desde}')
          AND date <= toDate('{fecha_hasta}')
        """
    )
    client.command(delete_filter)
    _wait_for_mutations(client, _config()["mutation_timeout"])

    return {
        "configured": True,
        "available": True,
        "range": {"fechaDesde": fecha_desde, "fechaHasta": fecha_hasta},
        "rowsRead": rows_read,
        "recordsInput": len(records),
        "recordsStored": inserted,
        "warning": warning,
        "emptyConfirmed": confirmed_empty,
        "origin": origin,
        "syncRunId": sync_run_id,
        "message": "Ventas sincronizadas en ClickHouse.",
    }


def load_erp_sales_dataset_clickhouse(fecha_desde, fecha_hasta):
    client = get_clickhouse_client()
    if client is None:
        raise RuntimeError("ClickHouse no está configurado")
    _ensure_schema()
    result = client.query(
        f"""
        SELECT
            date,
            year,
            month,
            client_key,
            client_name,
            route_description,
            seller_key,
            seller_name,
            sales_scheme_key,
            sales_scheme_name,
            sales_force,
            product_key,
            invoice,
            channel,
            amount,
            amount_net,
            amount_final,
            internal_taxes,
            amount_net_internal,
            quantity
        FROM {_qualified_table()}
        WHERE date >= toDate('{fecha_desde}')
          AND date <= toDate('{fecha_hasta}')
        ORDER BY date ASC
        """
    )
    records = []
    for row in result.result_rows:
        sale_date = row[0]
        if isinstance(sale_date, datetime):
            sale_date = sale_date.date()
        records.append(
            {
                "date": sale_date,
                "year": int(row[1]),
                "month": int(row[2]),
                "client_key": row[3],
                "client_name": row[4],
                "route_description": row[5],
                "seller_key": row[6],
                "seller_name": row[7],
                "sales_scheme_key": row[8],
                "sales_scheme_name": row[9],
                "sales_force": row[10],
                "product_key": row[11],
                "invoice": row[12],
                "channel": row[13],
                "amount": float(row[14]),
                "amount_net": float(row[15]),
                "amount_final": float(row[16]),
                "internal_taxes": float(row[17]),
                "amount_net_internal": float(row[18]),
                "quantity": float(row[19]),
                "source": "ClickHouse",
            }
        )
    if not records:
        raise ValueError("ClickHouse no tiene ventas ERP para el rango seleccionado")
    source_label = f"Base comercial {fecha_desde} a {fecha_hasta}"
    return {
        "datasetType": "sales",
        "sourceKind": "clickhouse",
        "file": source_label,
        "sheet": "Ventas comerciales",
        "headerRow": 0,
        "rowsRead": len(records),
        "rowsValid": len(records),
        "headers": [],
        "mapping": {},
        "records": records,
        "sourceCount": 1,
        "sources": [
            {
                "file": source_label,
                "sheet": "Ventas comerciales",
                "headerRow": 0,
                "rowsRead": len(records),
                "rowsValid": len(records),
                "sourceKind": "clickhouse",
            }
        ],
    }


def get_clickhouse_storage_status():
    if clickhouse_connect is None:
        return {
            "configured": False,
            "available": False,
            "connected": False,
            "message": "Falta instalar clickhouse-connect para habilitar ClickHouse.",
        }
    if not clickhouse_configured():
        return {
            "configured": False,
            "available": False,
            "connected": False,
            "message": "ClickHouse no está configurado.",
        }
    try:
        client = get_clickhouse_client()
        _ensure_schema()
        rows = client.query(f"SELECT count(), min(date), max(date) FROM {_qualified_table()}").result_rows[0]
        parts = client.query(
            f"""
            SELECT sum(rows), sum(bytes_on_disk)
            FROM system.parts
            WHERE database = '{_config()["database"]}'
              AND table = '{_config()["table"]}'
              AND active
            """
        ).result_rows[0]
        record_count = int(rows[0] or 0)
        period_start = rows[1].isoformat() if record_count and rows[1] else None
        period_end = rows[2].isoformat() if record_count and rows[2] else None
        return {
            "configured": True,
            "connected": True,
            "available": bool(record_count),
            "message": "Ventas históricas disponibles en ClickHouse." if record_count else "ClickHouse está listo pero todavía sin ventas.",
            "records": record_count,
            "periodStart": period_start,
            "periodEnd": period_end,
            "rows": int(parts[0] or 0),
            "storageBytes": int(parts[1] or 0),
            "storageMB": round((parts[1] or 0) / 1024 / 1024, 2),
            "database": _config()["database"],
            "table": _config()["table"],
        }
    except Exception as exc:
        return {
            "configured": True,
            "connected": False,
            "available": False,
            "message": f"No se pudo consultar ClickHouse: {exc}",
        }
