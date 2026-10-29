"""One-shot Windows catalog diagnostic. No business queries or sync execution.

Reads the full PUB dictionary (tables, columns and index definitions) with three
catalog operations so that sales, returns, customers, articles and stock tables
can be identified offline from their real column names, without guessing table
names first or returning to Windows for each candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "PUB"
MAX_TABLES = 5000
MAX_COLUMNS = 100_000
MAX_INDEX_ROWS = 100_000
FETCH_BATCH = 2000
QUERY_TIMEOUT_SECONDS = 30
# Catalog metadata only (OpenEdge system table): index names, uniqueness and
# ordered columns. Never selects rows from business tables.
INDEX_QUERY = (
    "SELECT TBL, IDXNAME, IDXTYPE, IDXSEQ, COLNAME "
    "FROM SYSPROGRESS.SYSINDEXES WHERE TBLOWNER = ?"
)


def safe_error(exc):
    # ODBC diagnostics can contain credentials: only expose a SQLSTATE if present.
    state = str(exc.args[0]) if exc.args else ""
    return {"code": "ODBC_OPERATION_FAILED",
            "sqlstate": state if re.fullmatch(r"[A-Z0-9]{5}", state) else None}


def save_report(path, report):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def fetch_bounded(cursor, limit):
    rows = []
    while len(rows) <= limit:
        batch = cursor.fetchmany(min(FETCH_BATCH, limit + 1 - len(rows)))
        if not batch:
            break
        rows.extend(batch)
    return rows[:limit], len(rows) > limit


def run_catalog(connection, method, limit, **kwargs):
    cursor = connection.cursor()
    try:
        getattr(cursor, method)(**kwargs)
        return fetch_bounded(cursor, limit)
    finally:
        with suppress(Exception):
            cursor.close()


def run_metadata_query(connection, sql, params, limit):
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        return fetch_bounded(cursor, limit)
    finally:
        with suppress(Exception):
            cursor.close()


def collect_catalog(driver, report, checkpoint):
    connection = None
    try:
        # Same DSN and credential source as the TMA connector. Never use the bridge
        # secret, rewrite credentials, import TMA scripts or execute their jobs.
        connection_string = f"DSN={{CHESS_CODENOA}};QueryTimeout={QUERY_TIMEOUT_SECONDS}"
        password = os.environ.get("CHESS_ODBC_PWD")
        if password:
            connection_string += ";PWD={" + password.replace("}", "}}") + "}"
        connection = driver.connect(connection_string, readonly=True, autocommit=False, timeout=10)
        report["connection"] = "connected"
        report["catalog_calls"] = 0
        with suppress(Exception):
            # As TMA does: never hold read locks that could block Chess users.
            connection.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")

        tables, truncated = run_catalog(connection, "tables", MAX_TABLES,
                                        schema=SCHEMA, tableType="TABLE,VIEW")
        report["catalog_calls"] += 1
        report["tables"] = {row.table_name: row.table_type for row in tables}
        report["tables_truncated"] = truncated
        checkpoint()

        columns, truncated = run_catalog(connection, "columns", MAX_COLUMNS, schema=SCHEMA)
        report["catalog_calls"] += 1
        by_table = {}
        for row in columns:
            by_table.setdefault(row.table_name, []).append(
                [row.column_name, row.type_name, row.column_size, row.decimal_digits,
                 row.nullable, row.ordinal_position])
        for entries in by_table.values():
            entries.sort(key=lambda entry: entry[5] or 0)
        report["columns"] = by_table
        report["columns_format"] = ["name", "type", "size", "decimal_digits", "nullable", "ordinal"]
        report["columns_truncated"] = truncated
        checkpoint()

        try:
            index_rows, truncated = run_metadata_query(connection, INDEX_QUERY, (SCHEMA,), MAX_INDEX_ROWS)
            report["catalog_calls"] += 1
            indexes = {}
            for table, name, kind, sequence, column in index_rows:
                entry = indexes.setdefault(table, {}).setdefault(
                    name, {"unique": str(kind).strip().upper() == "U", "columns": []})
                entry["columns"].append([sequence, column])
            for table_indexes in indexes.values():
                for entry in table_indexes.values():
                    entry["columns"] = [column for _, column in sorted(entry["columns"])]
            report["indexes"] = indexes
            report["indexes_truncated"] = truncated
        except Exception as exc:
            # Columns are still useful without index metadata.
            report["indexes_error"] = safe_error(exc)
        report["status"] = "catalog_collected" if tables else "no_visible_tables"
    except Exception as exc:
        report.update(status="partial_or_failed", error=safe_error(exc))
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.rollback()
            with suppress(Exception):
                connection.close()
        checkpoint()


def local_evidence(directory):
    evidence = {"files": [], "log_summaries": []}
    for name in ("chess_query.py", "push_available_stock.py", "push_chess_promotions.py"):
        path = directory / name
        record = {"name": name, "exists": path.is_file()}
        if path.is_file():
            record.update(size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        evidence["files"].append(record)
    for name in ("push_available_stock.log", "push_chess_promotions.log"):
        path = directory / name
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 16384))
            lines = stream.read(16384).decode("utf-8", errors="replace").splitlines()[-10:]
        # Only timestamps and status words; never raw errors, payloads or numbers.
        summaries = []
        for line in lines:
            match = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\s+(OK|ERROR)\b", line)
            if match:
                summaries.append({"timestamp_as_logged": match[1], "status": match[2]})
        evidence["log_summaries"].append({"file": name, "recent_statuses": summaries})
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {
        "schema_version": "chess-diagnostic/2", "status": "started",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "windows_local_time": datetime.now().astimezone().isoformat(),
        "business_rows_read": 0, "permissions_verified": False,
        "business_mapping_verified": False,
        "limits": {"tables": MAX_TABLES, "columns": MAX_COLUMNS, "index_rows": MAX_INDEX_ROWS,
                   "catalog_calls": 3, "query_timeout_seconds": QUERY_TIMEOUT_SECONDS},
        "environment_present": {key: bool(os.environ.get(key)) for key in
                                ("CHESS_ODBC_PWD", "CHESS_BRIDGE_SECRET")},
    }
    checkpoint = lambda: save_report(args.output, report)
    checkpoint()
    try:
        report["local_evidence"] = local_evidence(Path(r"C:\chess-odbc"))
        import pyodbc
        report["pyodbc_version"] = pyodbc.version
        report["dsn_visible"] = "CHESS_CODENOA" in pyodbc.dataSources()
        checkpoint()
        collect_catalog(pyodbc, report, checkpoint)
    except Exception as exc:
        report.update(status="collector_failed", error=safe_error(exc))
        checkpoint()
    return 0 if report["status"] == "catalog_collected" else 1


if __name__ == "__main__":
    raise SystemExit(main())
