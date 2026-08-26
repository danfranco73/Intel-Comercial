from __future__ import annotations

import pytest

import mongo_client
import supabase_client


class _BulkResult:
    def __init__(self, upserted_count, modified_count, matched_count):
        self.upserted_count = upserted_count
        self.modified_count = modified_count
        self.matched_count = matched_count


class FakeCursor(list):
    def sort(self, *_args, **_kwargs):
        return self


class FakeMasterCollection:
    """Doble liviano de una colección Mongo, sin depender de mongomock.

    Nota: en este entorno `mongomock==4.3.0` es incompatible con
    `pymongo==4.17.0` para `bulk_write` (le pasa un kwarg `sort` que
    mongomock no acepta) — es un problema preexistente, no algo introducido
    acá, y afecta a CUALQUIER test que ejercite `_sync_simple_master` (el
    helper que ya usan `sync_erp_sellers`/`sync_erp_routes`/`sync_erp_marketing`
    desde antes). Este fake evita el bug reimplementando solo lo que
    `_sync_simple_master` necesita, para poder testear la lógica real."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

    def delete_many(self, _filter):
        count = len(self.docs)
        self.docs.clear()
        return type("_DeleteResult", (), {"deleted_count": count})()

    def bulk_write(self, operations, ordered=False):
        upserted = modified = matched = 0
        for op in operations:
            key = op._filter["_id"]
            doc = dict(op._doc["$set"])
            if key in self.docs:
                matched += 1
                modified += 1
            else:
                upserted += 1
            self.docs[key] = doc
        return _BulkResult(upserted, modified, matched)

    def insert_one(self, doc):
        self.docs[f"__sync_run_{len(self.docs)}"] = doc

    def find(self, _filter=None, projection=None):
        exclude = {k for k, v in (projection or {}).items() if v == 0}
        rows = []
        for doc in self.docs.values():
            row = {k: v for k, v in doc.items() if k not in exclude}
            rows.append(row)
        return FakeCursor(rows)

    def find_one(self, *_args, **_kwargs):
        return next(iter(self.docs.values()), None)

    def create_index(self, *_args, **_kwargs):
        return None


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = FakeMasterCollection()
        return super().__getitem__(name)


@pytest.fixture()
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(mongo_client, "get_db", lambda: db)
    monkeypatch.setattr(mongo_client, "_ensure_erp_indexes", lambda _db: None)
    return db


def test_sync_erp_clients_replaces_and_keys_by_client_key(fake_db):
    records = [
        {"client_key": "101", "company": "Distribuidora Norte", "credit_limit": 50000.0},
        {"client_key": "102", "company": "Kiosco Sur", "credit_limit": 15000.0},
    ]
    summary = mongo_client.sync_erp_clients(records, origin="test")
    assert summary["upserted"] == 2

    stored = fake_db[mongo_client.ERP_CLIENTS_COLLECTION].docs
    assert set(stored.keys()) == {"101", "102"}

    # Un segundo sync con menos registros debe reemplazar (replace_all=True).
    mongo_client.sync_erp_clients([{"client_key": "101", "company": "Distribuidora Norte"}], origin="test")
    assert set(fake_db[mongo_client.ERP_CLIENTS_COLLECTION].docs.keys()) == {"101"}


def test_sync_products_commercial_is_separate_from_erp_articles(fake_db):
    fake_db[mongo_client.ERP_ARTICLES_COLLECTION].docs["555"] = {
        "product_key": "555",
        "product_name": "Gaseosa 2L",
        "business_unit": "Bebidas",
    }

    mongo_client.sync_products_commercial(
        [{"product_key": "555", "price": 1200.5, "stock": 40.0}], origin="test"
    )

    article = fake_db[mongo_client.ERP_ARTICLES_COLLECTION].docs["555"]
    commercial = fake_db[mongo_client.ERP_PRODUCTS_COMMERCIAL_COLLECTION].docs["555"]
    assert article["business_unit"] == "Bebidas"
    assert "price" not in article
    assert commercial["price"] == 1200.5


def test_load_erp_clients_dataset_round_trips(fake_db):
    mongo_client.sync_erp_clients([{"client_key": "9", "company": "Almacén Test"}], origin="test")
    dataset = mongo_client.load_erp_clients_dataset()
    assert dataset["datasetType"] == "clients"
    assert dataset["sourceKind"] == "mongo"
    assert dataset["records"][0]["client_key"] == "9"
    assert "_id" not in dataset["records"][0]


def test_load_products_commercial_dataset_raises_when_empty(fake_db):
    with pytest.raises(ValueError):
        mongo_client.load_products_commercial_dataset()


def test_normalize_supabase_product_row_maps_erp_id_to_product_key():
    row = {
        "id": "uuid-1",
        "erp_id": "12345",
        "title": "Aceite 900ml",
        "price": 890.0,
        "internal_tax": 12.5,
        "stock": 30,
        "unit": "unidad",
        "units_per_pack": 12,
        "category": "Almacén",
        "is_active": True,
        "supplier": "Proveedor SA",
        "brand": "Marca X",
        "calibre": "900ml",
        "product_segment": "Premium",
        "family_name": "Aceites",
    }
    record = supabase_client.normalize_supabase_product_row(row, price_lists=[{"price_list": "1", "price": 890.0}])
    assert record["product_key"] == "12345"
    assert record["product_name"] == "Aceite 900ml"
    assert record["family"] == "Aceites"
    assert record["price_lists"] == [{"price_list": "1", "price": 890.0}]
    assert record["source"] == "Supabase"


def test_normalize_supabase_product_row_skips_rows_without_erp_id():
    assert supabase_client.normalize_supabase_product_row({"id": "uuid-2", "erp_id": None}, price_lists=[]) is None


def test_normalize_supabase_client_row_maps_client_code_to_client_key():
    row = {
        "client_code": "778",
        "company": "Cliente SRL",
        "credit_limit": 100000,
        "overdue_debt_days": 0,
        "assigned_seller_code": "35",
    }
    record = supabase_client.normalize_supabase_client_row(row)
    assert record["client_key"] == "778"
    assert record["assigned_seller_key"] == "35"
    assert record["source"] == "Supabase"
