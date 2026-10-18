from contextlib import contextmanager
from datetime import date
import threading
import tracemalloc

import mongomock
import pytest

import clickhouse_client as ch
import sales_coach.intelligence_budget as limits
from sales_coach.repositories.sales_repository import SalesRepository
from sales_coach.services.intelligence_sales_service import IntelligenceSalesService
from sales_coach.services.intelligence_context_service import IntelligenceContextService
from test_intelligence_sales import admin, setup


class Cursor:
    def __init__(self, count, row=None):
        self.count, self.row, self.read, self.closed = count, row, 0, False
        self.options = {}
    def limit(self, n): self.options['limit'] = n; self.count = min(self.count, n); return self
    def batch_size(self, n): self.options['batch'] = n; return self
    def max_time_ms(self, n): self.options['time'] = n; return self
    def __iter__(self):
        for index in range(self.count):
            self.read += 1
            yield self.row or {'date': '2026-06-15', 'company_key': 'SYNTH',
                               'client_key': str(index), 'amount': 1, 'quantity': 1}
    def close(self): self.closed = True


class Collection:
    def __init__(self, cursor): self.cursor = cursor
    def find(self, *args, **kwargs): return self.cursor


class Database:
    def __init__(self, cursor): self.collection = Collection(cursor)
    def __getitem__(self, key): return self.collection


@pytest.mark.parametrize('operation', ['brief', 'drilldown'])
def test_huge_source_is_stopped_before_enrichment_and_no_partial_report(monkeypatch, operation):
    monkeypatch.setattr(ch, 'clickhouse_configured', lambda: False)
    cursor = Cursor(10_000_000)
    db = Database(cursor)
    monkeypatch.setattr('sales_coach.services.intelligence_context_service.enrich_sales_records',
                        lambda *_: pytest.fail('Oversized detail reached enrichment'))
    tracemalloc.start()
    try:
        with pytest.raises(limits.IntelligenceCapacityExceeded):
            getattr(IntelligenceSalesService(db), operation)({'as_of': '2026-06-30'}, admin())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert cursor.read == limits.MAX_ROWS + 1
    assert cursor.closed and cursor.options == dict(limit=5001, batch=1, time=2000)
    print(f"{operation}: generated=10000000 consumed={cursor.read} peak_bytes={peak}")
    assert peak < 16 * 1024 * 1024


def test_clickhouse_limit_closes_stream_and_applies_server_caps():
    class Client:
        closed = False
        @contextmanager
        def query_row_block_stream(self, query):
            self.query = query
            try:
                yield ([('2026-01-01', 1)] for _ in range(10_000_000))
            finally:
                self.closed = True
    client = Client()
    @limits.bounded_calculation
    def read(): return list(ch._stream_rows(client, 'SELECT date, count() FROM synthetic GROUP BY date'))
    with pytest.raises(limits.IntelligenceCapacityExceeded): read()
    assert client.closed
    for text in ('max_memory_usage=134217728', 'max_execution_time=2',
                 "result_overflow_mode='throw'", 'max_result_rows=5001', 'max_threads=1'):
        assert text in client.query


def test_clickhouse_failure_never_repeats_in_mongo(monkeypatch):
    monkeypatch.setattr(ch, 'clickhouse_configured', lambda: True)
    def fail(*args, **kwargs): raise RuntimeError('synthetic source failure')
    monkeypatch.setattr('sales_coach.repositories.sales_repository.load_erp_sales_dataset_clickhouse', fail)
    class ForbiddenDB:
        def __getitem__(self, key): pytest.fail('Fallback read')
    with pytest.raises(RuntimeError): SalesRepository().load_intelligence(ForbiddenDB(),'2026-01-01','2026-06-30')


def test_master_overflow_denies_before_engine(monkeypatch):
    db = Database(Cursor(10_000_000))
    context = IntelligenceContextService(db, loader=lambda *_: ([], 'mongo', []))
    with pytest.raises(limits.IntelligenceCapacityExceeded):
        IntelligenceSalesService(db, context).brief({'as_of':'2026-06-30'},admin())


def test_single_large_document_and_total_byte_limits():
    budget = limits.Budget()
    with pytest.raises(limits.IntelligenceCapacityExceeded): budget.consume({'value':'x'*300000})
    budget = limits.Budget()
    with pytest.raises(limits.IntelligenceCapacityExceeded):
        for _ in range(100): budget.consume({'value':'x'*200000})
    assert budget.rows < 100


def test_timeout_cleans_budget_and_releases_admission(monkeypatch):
    @limits.bounded_calculation
    def expired(): limits.current_budget().deadline = 0; return {'fake': 'report'}
    with pytest.raises(limits.IntelligenceCapacityExceeded): expired()
    assert limits.current_budget() is None
    @limits.bounded_calculation
    def next_request(): return 'ok'
    assert next_request() == 'ok'


def test_concurrent_request_fails_without_waiting_or_loading():
    entered, release = threading.Event(), threading.Event()
    @limits.bounded_calculation
    def blocking(): entered.set(); release.wait(2)
    worker = threading.Thread(target=blocking)
    worker.start()
    assert entered.wait(1)
    try:
        @limits.bounded_calculation
        def other(): pytest.fail('Concurrent request entered')
        with pytest.raises(limits.IntelligenceCapacityExceeded): other()
    finally:
        release.set(); worker.join(3)
    assert not worker.is_alive()


def test_mongo_contract_totals_stay_exact_and_context_stable(datasets, monkeypatch):
    db, service = setup(datasets)
    records = service.context_service.loader(None,None,None)[0]
    # Exercise the actual bounded Mongo path with the same full fixture universe.
    for row in records:
        db.erp_sales.insert_one({**row, 'date':row['date'].isoformat()})
    monkeypatch.setattr(ch, 'clickhouse_configured', lambda: False)
    db.sync_runs.update_one({}, {"$set": {"rows_stored.mongo": len(records)}})
    coverage = service.context_service.coverage_service
    bounded_context = IntelligenceContextService(db, coverage_service=coverage)
    bounded = IntelligenceSalesService(db, bounded_context)
    # Coverage fixture still certifies the same source counts; compare metrics,
    # not the source label included in the evidence identity.
    for dimension in ('company', 'seller', 'client', 'product'):
        query={'as_of':'2026-06-30','dimension':dimension}
        expected=service.drilldown(query,admin())
        actual=bounded.drilldown(query,admin())
        assert actual['nodes']==expected['nodes']
        assert actual['totals']==expected['totals']
        repeated=bounded.drilldown({**query,'context_id':actual['context_id']},admin())
        assert repeated==actual


def test_capacity_error_http_body_is_explicit_and_sanitized():
    from sales_coach.routes.intelligence_routes import IntelligenceRoutesMixin
    class Handler(IntelligenceRoutesMixin):
        def send_json(self, value, status=200): self.value,self.status=value,status
    handler=Handler()
    def fail(): raise limits.IntelligenceCapacityExceeded('DO_NOT_EXPOSE')
    handler._intelligence_call(fail)
    assert handler.status==503
    assert handler.value['code']=='INTELLIGENCE_CAPACITY_EXCEEDED'
    assert 'DO_NOT_EXPOSE' not in str(handler.value)
    assert 'summary' not in handler.value and 'nodes' not in handler.value
