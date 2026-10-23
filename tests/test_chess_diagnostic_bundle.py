from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import HTTPServer
from types import SimpleNamespace

import pytest

from scripts.windows import collect_chess_diagnostics as collector
from scripts import receive_chess_diagnostics as receiver


class CatalogCursor:
    """Consumes rows like a real cursor so batched fetching is exercised."""

    def __init__(self, connection):
        self.connection = connection
        self.pending = []

    def _load(self, method, rows):
        self.connection.calls.append(method)
        if method == self.connection.fail_on:
            raise RuntimeError('PWD=private-password; UID=private-user')
        self.pending = list(rows)

    def tables(self, **kwargs):
        self._load('tables', self.connection.table_rows)

    def columns(self, **kwargs):
        self._load('columns', self.connection.column_rows)

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        if self.connection.fail_index_query:
            raise RuntimeError('42S02', 'driver detail with PWD=private-password')
        self.pending = list(self.connection.index_rows)

    def fetchmany(self, size):
        batch, self.pending = self.pending[:size], self.pending[size:]
        return batch

    def close(self):
        pass


class CatalogConnection:
    def __init__(self):
        self.calls = []
        self.executed = []
        self.closed = False
        self.fail_on = None
        self.fail_index_query = False
        self.table_rows = [SimpleNamespace(table_name='stkactual', table_type='TABLE')]
        self.column_rows = [
            SimpleNamespace(table_name='stkactual', column_name='cant', type_name='DECIMAL',
                            column_size=12, decimal_digits=2, nullable=1, ordinal_position=3),
            SimpleNamespace(table_name='stkactual', column_name='codart', type_name='INTEGER',
                            column_size=10, decimal_digits=0, nullable=0, ordinal_position=2),
        ]
        self.index_rows = [('stkactual', 'stkactual-pk', 'U', 2, 'codart'),
                           ('stkactual', 'stkactual-pk', 'U', 1, 'iddepo')]

    def execute(self, sql):
        self.executed.append((sql, ()))

    def cursor(self):
        return CatalogCursor(self)

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_catalog_reads_dictionary_readonly_without_leaking_password(monkeypatch):
    connection = CatalogConnection()
    calls = []
    monkeypatch.setenv('CHESS_ODBC_PWD', 'private};key=value')
    driver = SimpleNamespace(connect=lambda *args, **kwargs: (calls.append((args, kwargs)) or connection))
    report = {}
    collector.collect_catalog(driver, report, lambda: None)
    assert calls[0][1] == {'readonly': True, 'autocommit': False, 'timeout': 10}
    assert calls[0][0][0] == 'DSN={CHESS_CODENOA};QueryTimeout=30;PWD={private}};key=value}'
    assert report['status'] == 'catalog_collected'
    assert report['tables'] == {'stkactual': 'TABLE'}
    assert [column[0] for column in report['columns']['stkactual']] == ['codart', 'cant']
    assert report['indexes']['stkactual']['stkactual-pk'] == {'unique': True, 'columns': ['iddepo', 'codart']}
    assert report['catalog_calls'] == 3
    assert connection.executed[0] == ('SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED', ())
    # Only catalog metadata is queried; never business tables.
    assert all('SYSPROGRESS.SYSINDEXES' in sql for sql, _ in connection.executed[1:])
    assert 'private' not in json.dumps(report)
    assert connection.closed


def test_index_metadata_failure_keeps_columns_and_hides_driver_message():
    connection = CatalogConnection()
    connection.fail_index_query = True
    report = {}
    collector.collect_catalog(SimpleNamespace(connect=lambda *args, **kwargs: connection), report, lambda: None)
    assert report['status'] == 'catalog_collected'
    assert 'stkactual' in report['columns']
    assert report['indexes_error'] == {'code': 'ODBC_OPERATION_FAILED', 'sqlstate': '42S02'}
    assert 'private' not in json.dumps(report)


def test_fetch_is_bounded_and_flags_truncation():
    class Cursor:
        def __init__(self):
            self.remaining = 25
            self.sizes = []

        def fetchmany(self, size):
            self.sizes.append(size)
            take = min(size, self.remaining)
            self.remaining -= take
            return list(range(take))

    cursor = Cursor()
    rows, truncated = collector.fetch_bounded(cursor, 10)
    assert len(rows) == 10 and truncated is True
    assert sum(cursor.sizes) <= 11


def test_failure_keeps_partial_report_without_driver_message():
    connection = CatalogConnection()
    connection.fail_on = 'columns'
    report = {}
    checkpoints = []
    collector.collect_catalog(SimpleNamespace(connect=lambda *args, **kwargs: connection), report,
                              lambda: checkpoints.append(json.dumps(report)))
    assert report['status'] == 'partial_or_failed'
    assert report['tables'] == {'stkactual': 'TABLE'}
    assert report['error']['sqlstate'] is None
    assert 'private-password' not in json.dumps(report)
    assert checkpoints and connection.closed


def test_local_evidence_excludes_error_text_and_payloads(tmp_path):
    (tmp_path / 'push_available_stock.log').write_text(
        '2026-10-15T18:02:17\tOK\tfilas=99\tprivate-payload\n'
        '2026-10-15T18:12:17\tERROR\tPWD=private-password\n')
    (tmp_path / 'chess_query.py').write_text('private-source')
    evidence = collector.local_evidence(tmp_path)
    assert evidence['files'][0]['exists'] is True
    assert evidence['log_summaries'][0]['recent_statuses'][-1]['status'] == 'ERROR'
    assert 'private' not in json.dumps(evidence)


@pytest.mark.parametrize('address', ['0.0.0.0', '8.8.8.8', '192.168.1.100'])
def test_receiver_rejects_public_or_unscoped_bind(address):
    with pytest.raises(ValueError):
        receiver.allowed_bind(address)


def test_script_contains_collector_and_no_unexpanded_placeholders():
    script = receiver.render_script('http://100.100.214.121:12345/test').decode()
    assert '__RECEIVER__' not in script and '__COLLECTOR_BASE64__' not in script
    assert 'CHESS_BRIDGE_SECRET' not in script  # Only encoded collector checks presence.
    assert '$process.Kill()' in script
    assert 'Stop-ScheduledTask' not in script
    assert 'SetEnvironmentVariable' not in script
    assert "--noproxy '*'" in script
    assert '--max-time 120' in script
    assert 'ConvertFrom-Json' not in script.split('$catalogJson')[1].split('$meta')[0]
    assert 'Start-Service' not in script and 'Set-Service' not in script


@pytest.fixture
def http_receiver(tmp_path):
    target = tmp_path / 'report.json'
    handler = receiver.make_handler('127.0.0.1', 'test-token', b'reviewed script', target)
    server = HTTPServer(('127.0.0.1', 0), handler)
    server.report_received = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, target
    server.shutdown()
    server.server_close()
    thread.join()


def request(server, method, path, body=None, headers=None):
    client = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    try:
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


def test_receiver_accepts_one_report_and_does_not_serve_it(http_receiver):
    server, target = http_receiver
    assert request(server, 'GET', '/test-token/collect.ps1') == (200, b'reviewed script')
    assert json.loads((target.parent / 'progress.json').read_text())['stage'] == 'script_requested'
    assert request(server, 'GET', '/wrong/collect.ps1')[0] == 404
    assert request(server, 'POST', '/test-token/report', '{}')[0] == 400
    report = json.dumps({'schema_version': 'chess-diagnostic-bundle/2', 'catalog': {}})
    assert request(server, 'POST', '/test-token/report', report)[0] == 200
    assert server.report_received
    assert json.loads(target.read_text())['catalog'] == {}
    assert request(server, 'GET', '/test-token/report')[0] == 404
    assert request(server, 'POST', '/test-token/report', report)[0] == 409


def test_receiver_rejects_oversized_report(http_receiver):
    server, target = http_receiver
    assert request(server, 'POST', '/test-token/report', '',
                   {'Content-Length': str(receiver.MAX_REPORT_BYTES + 1)})[0] == 413
    assert not target.exists()
