"""Bounded ODBC catalog inspection; deliberately independent of the sync pipeline."""
from __future__ import annotations

import os
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic


class OdbcInspectionError(RuntimeError):
    """Messages are safe to print: never include driver diagnostics or secrets."""


@dataclass(frozen=True)
class OdbcConfig:
    dsn: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    login_timeout: int = 10
    query_timeout: int = 15

    def __post_init__(self):
        for value in (self.dsn, self.username, self.password):
            if not value or any(char in value for char in "\x00\r\n"):
                raise OdbcInspectionError("Configurar CHESS_ODBC_DSN, CHESS_ODBC_USER y CHESS_ODBC_PASSWORD válidos.")
        for value in (self.login_timeout, self.query_timeout):
            if type(value) is not int or not 1 <= value <= 120:
                raise OdbcInspectionError("Los timeouts ODBC deben estar entre 1 y 120 segundos.")

    @classmethod
    def from_env(cls):
        # Do not load the application's .env or import any production connectors.
        # Reuse the names already used by C:\\ChessBI\\api_chess.py.
        return cls(os.environ.get("CHESS_ODBC_DSN", "CHESS_CODENOA"),
                   os.environ.get("CHESS_ODBC_USER", ""),
                   os.environ.get("CHESS_ODBC_PASSWORD", ""))

    def connection_string(self):
        def quote(value):
            return "{" + value.replace("}", "}}") + "}"
        credentials = ";".join(f"{key}={quote(value)}" for key, value in
                               (("DSN", self.dsn), ("UID", self.username), ("PWD", self.password)))
        # Use the DataDirect option used by TMA's existing OpenEdge connector.
        # Setting connection.timeout is not supported by that installation.
        return f"{credentials};QueryTimeout={self.query_timeout}"


@dataclass(frozen=True)
class InspectionScope:
    schema: str
    table_pattern: str
    max_tables: int = 5
    max_columns: int = 200

    def __post_init__(self):
        for value in (self.schema, self.table_pattern):
            if not value.strip() or len(value) > 128 or any(c in value for c in "\x00\r\n"):
                raise OdbcInspectionError("Indicar esquema y patrón de tablas de hasta 128 caracteres.")
        if not any(c.isalnum() for c in self.table_pattern):
            raise OdbcInspectionError("El patrón debe incluir parte del nombre; no se admite un catálogo global.")
        if type(self.max_tables) is not int or not 1 <= self.max_tables <= 20:
            raise OdbcInspectionError("max_tables debe estar entre 1 y 20.")
        if type(self.max_columns) is not int or not 1 <= self.max_columns <= 500:
            raise OdbcInspectionError("max_columns debe estar entre 1 y 500.")


@contextmanager
def inspection_lock(path: Path):
    """Atomic directory lock, shared by probes under the same OS account.

    A killed process leaves the lock in place: never guess that it is safe to
    remove it while another driver call may still be running.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError:
        raise OdbcInspectionError("Existe un bloqueo ODBC; verificar la otra ejecución antes de retirarlo.") from None
    try:
        yield
    finally:
        path.rmdir()


def _load_driver():
    try:
        import pyodbc
    except (ImportError, OSError):
        raise OdbcInspectionError("Instalar requirements-odbc.txt y verificar el driver ODBC del equipo.") from None
    return pyodbc


def _literal_pattern(value, escape):
    if value is None:
        return None
    # Catalog functions interpret '_' and '%' as wildcards. Escape names returned
    # by SQLTables before passing them to SQLColumns, including the escape itself.
    return "".join(escape + c if c in (escape, "%", "_") else c for c in value)


def _catalog_rows(connection, method, limit, fields, **kwargs):
    cursor = connection.cursor()
    try:
        getattr(cursor, method)(**kwargs)
        rows = cursor.fetchmany(limit + 1)
        return [{name: getattr(row, name) for name in fields} for row in rows[:limit]], len(rows) > limit
    finally:
        cursor.close()


def inspect_schema(config: OdbcConfig, scope: InspectionScope, *, lock_path: Path | None = None):
    """Read SQLTables/SQLColumns/SQLPrimaryKeys only. No SQL execution or retries.

    readonly=True requests driver read-only mode; database grants must still be
    verified independently. Row limits bound consumption, not server scan cost.
    """
    lock_path = lock_path or Path.home() / ".intelcomercial" / "odbc-inspection.lock"
    connection = None
    started = monotonic()
    try:
        with inspection_lock(lock_path):
            driver = _load_driver()
            try:
                connection = driver.connect(config.connection_string(), readonly=True,
                                            autocommit=True, timeout=config.login_timeout)
                escape = connection.searchescape
                if not isinstance(escape, str) or len(escape) != 1:
                    raise OdbcInspectionError("El driver no informa un escape de catálogo compatible.")
                tables, truncated = _catalog_rows(
                    connection, "tables", scope.max_tables,
                    ("table_cat", "table_schem", "table_name", "table_type"),
                    schema=_literal_pattern(scope.schema, escape),
                    table=scope.table_pattern, tableType="TABLE,VIEW",
                )
                for table in tables:
                    columns, columns_truncated = _catalog_rows(
                        connection, "columns", scope.max_columns,
                        ("column_name", "type_name", "column_size", "decimal_digits", "nullable", "ordinal_position"),
                        catalog=table["table_cat"],
                        schema=_literal_pattern(table["table_schem"], escape),
                        table=_literal_pattern(table["table_name"], escape),
                    )
                    keys, keys_truncated = _catalog_rows(
                        connection, "primaryKeys", scope.max_columns,
                        ("column_name", "key_seq", "pk_name"),
                        catalog=table["table_cat"], schema=table["table_schem"], table=table["table_name"],
                    )
                    table.update(columns=columns, primary_keys=keys,
                                 columns_truncated=columns_truncated, keys_truncated=keys_truncated)
                    truncated = truncated or columns_truncated or keys_truncated
                return {
                    "schema_version": "odbc-catalog/1",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": round((monotonic() - started) * 1000),
                    "status": "truncated" if truncated else "inspected" if tables else "no_matches",
                    "scope": {"schema": scope.schema, "table_pattern": scope.table_pattern},
                    "tables": tables,
                    "truncated": truncated,
                    "readonly_requested": True,
                    "permissions_verified": False,
                    "business_mapping_verified": False,
                    "business_rows_read": 0,
                    "catalog_calls": 1 + 2 * len(tables),
                }
            finally:
                if connection is not None:
                    with suppress(Exception):
                        connection.close()
    except OdbcInspectionError:
        raise
    except Exception:
        # Driver messages can embed passwords, connection strings and addresses.
        raise OdbcInspectionError("Falló la inspección ODBC; revisar conexión, permisos y compatibilidad en el equipo de origen.") from None
