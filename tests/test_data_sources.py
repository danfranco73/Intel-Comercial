from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, local

import clickhouse_client
import mongo_client


class FakeCursor(list):
    def sort(self, *_args, **_kwargs):
        return self


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.rows)


class FakeDB(dict):
    def __getitem__(self, name):
        return super().__getitem__(name)


def test_load_from_mongo(monkeypatch, sales_records):
    documents = []
    for index, record in enumerate(sales_records):
        document = dict(record)
        document["_id"] = str(index)
        document["date"] = record["date"].isoformat()
        documents.append(document)
    fake_db = FakeDB(erp_sales=FakeCollection(documents))
    monkeypatch.setattr(mongo_client, "get_db", lambda: fake_db)
    monkeypatch.setattr(mongo_client, "_ensure_erp_indexes", lambda _db: None)

    dataset = mongo_client.load_erp_sales_dataset("2026-05-01", "2026-06-30", require_coverage=False)
    assert dataset["rowsValid"] == 5
    assert dataset["records"][0]["date"] == date(2026, 5, 10)


class QueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClickHouse:
    def query(self, _query):
        return QueryResult(
            [
                (
                    date(2026, 6, 10), 2026, 6, "C1", "Cliente Uno", "Ruta Norte",
                    "S1", "Vendedora Norte", "F1", "Minorista", "Minorista", "P1",
                    "A-2", "Tradicional", 150.0, 135.0, 150.0, 7.5, 142.5, 15.0,
                )
            ]
        )


def test_load_from_clickhouse(monkeypatch):
    monkeypatch.setattr(clickhouse_client, "get_clickhouse_client", lambda: FakeClickHouse())
    monkeypatch.setattr(clickhouse_client, "_ensure_schema", lambda: None)
    dataset = clickhouse_client.load_erp_sales_dataset_clickhouse("2026-06-01", "2026-06-30")
    assert dataset["rowsValid"] == 1
    assert dataset["records"][0]["seller_key"] == "S1"


def test_clickhouse_client_is_not_shared_between_server_threads(monkeypatch):
    class Factory:
        @staticmethod
        def get_client(**_kwargs):
            return object()

    monkeypatch.setattr(clickhouse_client, "clickhouse_connect", Factory())
    monkeypatch.setattr(clickhouse_client, "clickhouse_configured", lambda: True)
    monkeypatch.setattr(clickhouse_client, "_client_state", local())
    barrier = Barrier(2)

    def resolve_client(_index):
        barrier.wait()
        first = clickhouse_client.get_clickhouse_client()
        assert first is clickhouse_client.get_clickhouse_client()
        return first

    with ThreadPoolExecutor(max_workers=2) as pool:
        clients = list(pool.map(resolve_client, range(2)))

    assert clients[0] is not clients[1]
