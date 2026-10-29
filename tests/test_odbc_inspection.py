from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import sales_coach.odbc_inspection as odbc
import scripts.inspect_chess_odbc as cli


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.closed = False

    def _catalog(self, method, kwargs):
        self.connection.calls.append((method, kwargs))
        if method == self.connection.fail_on:
            raise RuntimeError("PWD=private-password; UID=private-user; DSN=private-dsn")
        self.rows = list(self.connection.rows[method])
        return self

    def tables(self, **kwargs):
        return self._catalog("tables", kwargs)

    def columns(self, **kwargs):
        return self._catalog("columns", kwargs)

    def primaryKeys(self, **kwargs):
        return self._catalog("primaryKeys", kwargs)

    def fetchmany(self, count):
        self.connection.fetch_sizes.append(count)
        return self.rows[:count]

    def close(self):
        self.closed = True


class FakeConnection:
    searchescape = "\\"

    @property
    def timeout(self):
        raise NotImplementedError("DataDirect uses the QueryTimeout connection option")

    @timeout.setter
    def timeout(self, value):
        raise NotImplementedError("DataDirect uses the QueryTimeout connection option")

    def __init__(self):
        self.closed = False
        self.fail_on = None
        self.calls = []
        self.cursors = []
        self.fetch_sizes = []
        self.rows = {
            "tables": [SimpleNamespace(table_cat=None, table_schem="report_schema",
                                      table_name="sale_lines", table_type="TABLE")],
            "columns": [SimpleNamespace(column_name="line_id", type_name="INTEGER",
                                       column_size=10, decimal_digits=0, nullable=0, ordinal_position=1)],
            "primaryKeys": [SimpleNamespace(column_name="line_id", key_seq=1, pk_name="pk_sale")],
        }

    def cursor(self):
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def close(self):
        self.closed = True


@pytest.fixture
def driver(monkeypatch):
    connection = FakeConnection()
    calls = []

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return connection

    monkeypatch.setattr(odbc, "_load_driver", lambda: SimpleNamespace(connect=connect))
    return connection, calls


def config():
    return odbc.OdbcConfig("private-dsn", "private-user", "private-password")


def scope(**kwargs):
    return odbc.InspectionScope("report_schema", "sale%", **kwargs)


def test_offline_cli_never_loads_config_or_driver(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline command must not open a connection or load secrets")
    monkeypatch.setattr(odbc, "_load_driver", forbidden)
    monkeypatch.setattr(odbc.OdbcConfig, "from_env", forbidden)
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)["connection_attempted"] is False


def test_live_cli_requires_bounded_scope_before_loading_credentials(monkeypatch, capsys):
    monkeypatch.setattr(odbc.OdbcConfig, "from_env", lambda: pytest.fail("invalid scope"))
    for args in (["--inspect-schema"],
                 ["--inspect-schema", "--schema", "report", "--table-pattern", "%"]):
        assert cli.main(args) == 1
        assert json.loads(capsys.readouterr().err)["status"] == "failed"


def test_catalog_only_preserves_names_and_closes_resources(driver, tmp_path):
    connection, calls = driver
    lock = tmp_path / "inspection.lock"
    result = odbc.inspect_schema(config(), scope(), lock_path=lock)
    assert calls[0][1] == {"readonly": True, "autocommit": True, "timeout": 10}
    assert calls[0][0][0].endswith(";QueryTimeout=15")
    assert result["status"] == "inspected"
    assert result["business_rows_read"] == 0
    assert result["permissions_verified"] is False
    assert result["business_mapping_verified"] is False
    assert result["catalog_calls"] == 3
    assert [name for name, _ in connection.calls] == ["tables", "columns", "primaryKeys"]
    assert connection.calls[0][1]["schema"] == "report\\_schema"
    assert connection.calls[1][1]["table"] == "sale\\_lines"
    # SQLPrimaryKeys uses identifiers rather than patterns.
    assert connection.calls[2][1]["table"] == "sale_lines"
    assert connection.fetch_sizes == [6, 201, 201]
    assert connection.closed and all(cursor.closed for cursor in connection.cursors)
    assert not lock.exists()
    assert "private-" not in json.dumps(result)


@pytest.mark.parametrize("method", ["tables", "columns", "primaryKeys"])
def test_truncation_is_visible_and_bounded(driver, tmp_path, method):
    connection, _ = driver
    connection.rows[method] *= 3
    result = odbc.inspect_schema(config(), scope(max_tables=1, max_columns=1),
                                 lock_path=tmp_path / "lock")
    assert result["status"] == "truncated"
    assert result["truncated"] is True
    assert len(result["tables"]) == 1
    assert result["catalog_calls"] == 3
    assert max(connection.fetch_sizes) == 2


def test_no_matches_is_not_a_certified_empty_business_range(driver, tmp_path):
    connection, _ = driver
    connection.rows["tables"] = []
    result = odbc.inspect_schema(config(), scope(), lock_path=tmp_path / "lock")
    assert result["status"] == "no_matches"
    assert result["catalog_calls"] == 1
    assert result["business_mapping_verified"] is False


@pytest.mark.parametrize("method", ["tables", "columns", "primaryKeys"])
def test_failure_redacts_driver_diagnostics_and_releases_resources(driver, tmp_path, method):
    connection, calls = driver
    connection.fail_on = method
    lock = tmp_path / "lock"
    with pytest.raises(odbc.OdbcInspectionError) as error:
        odbc.inspect_schema(config(), scope(), lock_path=lock)
    assert "private-" not in str(error.value)
    assert error.value.__suppress_context__ is True
    assert len(calls) == 1  # no automatic retries on the ERP
    assert connection.closed and all(cursor.closed for cursor in connection.cursors)
    assert not lock.exists()


def test_connection_failure_is_redacted(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("PWD=private-password")
    monkeypatch.setattr(odbc, "_load_driver", lambda: SimpleNamespace(connect=fail))
    with pytest.raises(odbc.OdbcInspectionError, match="Falló la inspección") as error:
        odbc.inspect_schema(config(), scope(), lock_path=tmp_path / "lock")
    assert "private-password" not in str(error.value)
    assert not (tmp_path / "lock").exists()


def test_existing_lock_prevents_connection_and_is_not_removed(driver, tmp_path):
    _, calls = driver
    lock = tmp_path / "lock"
    with odbc.inspection_lock(lock):
        with pytest.raises(odbc.OdbcInspectionError, match="bloqueo"):
            odbc.inspect_schema(config(), scope(), lock_path=lock)
        assert lock.exists()
    assert not calls
    assert not lock.exists()


def test_connection_values_are_escaped_and_config_repr_hides_credentials():
    value = "sensitive};PWD=injected;{"
    settings = odbc.OdbcConfig(value, value, value)
    escaped = "{sensitive}};PWD=injected;{}"
    assert settings.connection_string() == f"DSN={escaped};UID={escaped};PWD={escaped};QueryTimeout=15"
    assert "sensitive" not in repr(settings)
    assert odbc._literal_pattern("a_%\\b", "\\") == "a\\_\\%\\\\b"


def test_custom_query_timeout_is_passed_to_datadirect():
    settings = odbc.OdbcConfig("dsn", "user", "password", query_timeout=30)
    assert settings.connection_string().endswith(";QueryTimeout=30")


def test_reuses_existing_gateway_environment(monkeypatch):
    monkeypatch.delenv("CHESS_ODBC_DSN", raising=False)
    monkeypatch.setenv("CHESS_ODBC_USER", "gateway-user")
    monkeypatch.setenv("CHESS_ODBC_PASSWORD", "gateway-password")
    settings = odbc.OdbcConfig.from_env()
    assert settings.dsn == "CHESS_CODENOA"
    assert settings.username == "gateway-user"
    assert settings.password == "gateway-password"
    monkeypatch.setenv("CHESS_ODBC_DSN", "alternate-test-dsn")
    assert odbc.OdbcConfig.from_env().dsn == "alternate-test-dsn"


@pytest.mark.parametrize("value", [0, -1, 121, True])
def test_invalid_timeout_rejected(value):
    with pytest.raises(odbc.OdbcInspectionError):
        odbc.OdbcConfig("dsn", "user", "password", query_timeout=value)


@pytest.mark.parametrize("value", ["", "\x00", "bad\nsecret"])
def test_invalid_credentials_rejected_without_echo(value):
    with pytest.raises(odbc.OdbcInspectionError) as error:
        odbc.OdbcConfig("dsn", "user", value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("status,code", [("inspected", 0), ("truncated", 2), ("no_matches", 2)])
def test_cli_exit_status(monkeypatch, capsys, status, code):
    monkeypatch.setattr(odbc.OdbcConfig, "from_env", config)
    monkeypatch.setattr(cli, "inspect_schema", lambda *_: {"status": status})
    assert cli.main(["--inspect-schema", "--schema", "report", "--table-pattern", "sale%"] ) == code
    assert json.loads(capsys.readouterr().out)["status"] == status
