from __future__ import annotations

import os
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.errors import AutoReconnect, ConnectionFailure
from erp_master_builder import build_dataset

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_NAME = "Intel-Comercial"
_SESSION_ID = "default"   # sesión única por instalación
ERP_SALES_COLLECTION = "erp_sales"
ERP_ARTICLES_COLLECTION = "erp_articles"
ERP_SELLERS_COLLECTION = "erp_sellers"
ERP_ROUTES_COLLECTION = "erp_routes"
ERP_MARKETING_COLLECTION = "erp_marketing"
ERP_SYNC_COLLECTION = "erp_sync_runs"
DEFAULT_MONGO_WRITE_BATCH_SIZE = 400
DEFAULT_MONGO_WRITE_RETRIES = 4
DEFAULT_MONGO_WRITE_RETRY_DELAY = 0.75
_client = None


def get_db():
    """Devuelve la base de datos Intel-Comercial. Retorna None si MONGO_URI no está configurado."""
    global _client
    uri = os.getenv("MONGO_URI")
    if not uri:
        return None
    if _client is None:
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return _client[DB_NAME]


def _reset_client():
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None


def _mongo_write_batch_size():
    try:
        value = int(os.getenv("MONGO_WRITE_BATCH_SIZE") or DEFAULT_MONGO_WRITE_BATCH_SIZE)
    except (TypeError, ValueError):
        value = DEFAULT_MONGO_WRITE_BATCH_SIZE
    return max(50, value)


def _mongo_write_retries():
    try:
        value = int(os.getenv("MONGO_WRITE_RETRIES") or DEFAULT_MONGO_WRITE_RETRIES)
    except (TypeError, ValueError):
        value = DEFAULT_MONGO_WRITE_RETRIES
    return max(1, value)


def _mongo_write_retry_delay():
    try:
        value = float(os.getenv("MONGO_WRITE_RETRY_DELAY") or DEFAULT_MONGO_WRITE_RETRY_DELAY)
    except (TypeError, ValueError):
        value = DEFAULT_MONGO_WRITE_RETRY_DELAY
    return max(0.1, value)


def _run_mongo_write(operation):
    retries = _mongo_write_retries()
    delay = _mongo_write_retry_delay()
    last_error = None
    for attempt in range(1, retries + 1):
        db = get_db()
        if db is None:
            raise RuntimeError("MongoDB no está configurado")
        _ensure_erp_indexes(db)
        try:
            return operation(db)
        except (AutoReconnect, ConnectionFailure) as exc:
            last_error = exc
            _reset_client()
            if attempt >= retries:
                break
            time.sleep(delay * attempt)
    raise last_error


def _iter_batches(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _ensure_erp_indexes(db):
    db[ERP_SALES_COLLECTION].create_index([("date", ASCENDING)])
    _ensure_non_unique_index(db[ERP_SALES_COLLECTION], "row_version_1", [("row_version", ASCENDING)], sparse=True)
    db[ERP_SALES_COLLECTION].create_index([("line_key", ASCENDING)], sparse=True)
    db[ERP_SALES_COLLECTION].create_index([("invoice", ASCENDING)])
    db[ERP_ARTICLES_COLLECTION].create_index([("product_key", ASCENDING)], unique=True)
    db[ERP_ARTICLES_COLLECTION].create_index([("row_version", ASCENDING)], sparse=True)
    db[ERP_SELLERS_COLLECTION].create_index([("seller_key", ASCENDING)], sparse=True)
    db[ERP_SELLERS_COLLECTION].create_index([("seller_name", ASCENDING)])
    db[ERP_SELLERS_COLLECTION].create_index([("sales_force", ASCENDING)])
    db[ERP_ROUTES_COLLECTION].create_index([("route_key", ASCENDING)], sparse=True)
    db[ERP_ROUTES_COLLECTION].create_index([("route_description", ASCENDING)])
    db[ERP_ROUTES_COLLECTION].create_index([("seller_name", ASCENDING)])
    db[ERP_ROUTES_COLLECTION].create_index([("sales_force", ASCENDING)])
    db[ERP_MARKETING_COLLECTION].create_index([("marketing_key", ASCENDING)], unique=True)
    db[ERP_MARKETING_COLLECTION].create_index([("segment_name", ASCENDING)])
    db[ERP_MARKETING_COLLECTION].create_index([("channel_name", ASCENDING)])
    db[ERP_SYNC_COLLECTION].create_index([("timestamp", DESCENDING)])


def _fallback_sale_id(record: dict) -> str:
    return "|".join(
        [
            str(record.get("date") or ""),
            str(record.get("client_key") or ""),
            str(record.get("document_key") or ""),
            str(record.get("invoice") or ""),
            str(record.get("line_key") or ""),
            str(record.get("product_key") or ""),
            str(record.get("seller_key") or ""),
        ]
    )


def _sale_document_id(record: dict) -> str:
    line_key = record.get("line_key")
    if line_key:
        return "|".join(
            [
                str(record.get("date") or ""),
                str(record.get("client_key") or ""),
                str(record.get("invoice") or record.get("document_key") or ""),
                str(line_key),
                str(record.get("product_key") or ""),
            ]
        )
    return _fallback_sale_id(record)


def _serialize_sale_record(record: dict) -> dict:
    document = dict(record)
    sale_date = document.get("date")
    if isinstance(sale_date, date):
        document["date"] = sale_date.isoformat()
    document["_id"] = _sale_document_id(document)
    document["storedAt"] = datetime.now(timezone.utc)
    return document


def _deserialize_sale_record(document: dict) -> dict:
    record = dict(document)
    record.pop("_id", None)
    record.pop("storedAt", None)
    sale_date = record.get("date")
    if isinstance(sale_date, str):
        record["date"] = date.fromisoformat(sale_date)
    return record


def sync_erp_sales(records: list[dict], fecha_desde: str, fecha_hasta: str, origin: str = "manual") -> dict:
    operations = []
    row_versions = 0
    for record in records:
        document = _serialize_sale_record(record)
        if record.get("row_version") is not None:
            row_versions += 1
        operations.append(
            UpdateOne(
                {"_id": document["_id"]},
                {"$set": document},
                upsert=True,
            )
        )

    delete_result = _run_mongo_write(
        lambda db: db[ERP_SALES_COLLECTION].delete_many({"date": {"$gte": fecha_desde, "$lte": fecha_hasta}})
    )

    upserted = 0
    modified = 0
    matched = 0
    if operations:
        batch_size = _mongo_write_batch_size()
        for batch in _iter_batches(operations, batch_size):
            result = _run_mongo_write(
                lambda db, current_batch=batch: db[ERP_SALES_COLLECTION].bulk_write(current_batch, ordered=False)
            )
            upserted += result.upserted_count
            modified += result.modified_count
            matched += result.matched_count

    summary = {
        "range": {"fechaDesde": fecha_desde, "fechaHasta": fecha_hasta},
        "recordsReceived": len(records),
        "rowVersions": row_versions,
        "deleted": delete_result.deleted_count,
        "upserted": upserted,
        "modified": modified,
        "matched": matched,
        "timestamp": datetime.now(timezone.utc),
        "origin": origin,
    }
    _run_mongo_write(lambda db: db[ERP_SYNC_COLLECTION].insert_one({**summary, "entity": "sales"}))
    summary["timestamp"] = summary["timestamp"].isoformat()
    return summary


def record_erp_sync_summary(entity: str, summary: dict) -> dict:
    payload = dict(summary)
    payload["entity"] = entity
    payload["timestamp"] = datetime.now(timezone.utc)
    _run_mongo_write(lambda db: db[ERP_SYNC_COLLECTION].insert_one(payload))

    response = dict(payload)
    response.pop("_id", None)
    response["timestamp"] = response["timestamp"].isoformat()
    return response


def load_erp_sales_dataset(fecha_desde: str, fecha_hasta: str) -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("MongoDB no está configurado")
    _ensure_erp_indexes(db)

    covered, missing_start, missing_end = _sales_range_covered(db, fecha_desde, fecha_hasta)
    if not covered:
        if missing_start and missing_end:
            raise ValueError(
                f"MongoDB no tiene sincronizado completamente el rango {fecha_desde} a {fecha_hasta}. "
                f"Falta cubrir desde {missing_start} hasta {missing_end}. Sincronizá ese tramo desde ChessERP o analizá en modo ChessERP."
            )
        raise ValueError(
            f"MongoDB no tiene sincronizado completamente el rango {fecha_desde} a {fecha_hasta}. "
            "Sincronizá ese tramo desde ChessERP o analizá en modo ChessERP."
        )

    cursor = db[ERP_SALES_COLLECTION].find(
        {"date": {"$gte": fecha_desde, "$lte": fecha_hasta}},
        {"storedAt": 0},
    ).sort("date", ASCENDING)
    records = [_deserialize_sale_record(item) for item in cursor]
    if not records:
        raise ValueError("MongoDB no tiene ventas ERP para el rango seleccionado")

    source_label = f"MongoDB ERP ventas {fecha_desde} a {fecha_hasta}"
    return {
        "datasetType": "sales",
        "sourceKind": "mongo",
        "file": source_label,
        "sheet": "MongoDB erp_sales",
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
                "sheet": "MongoDB erp_sales",
                "headerRow": 0,
                "rowsRead": len(records),
                "rowsValid": len(records),
                "sourceKind": "mongo",
            }
        ],
    }


def _sales_range_covered(db, fecha_desde: str, fecha_hasta: str) -> tuple[bool, str | None, str | None]:
    start = date.fromisoformat(fecha_desde)
    end = date.fromisoformat(fecha_hasta)
    sync_runs = list(
        db[ERP_SYNC_COLLECTION].find(
            {
                "entity": "sales",
                "range.fechaDesde": {"$lte": fecha_hasta},
                "range.fechaHasta": {"$gte": fecha_desde},
            },
            {"_id": 0, "range": 1},
        )
    )
    intervals = []
    for run in sync_runs:
        current_range = run.get("range") or {}
        range_start = current_range.get("fechaDesde")
        range_end = current_range.get("fechaHasta")
        if not range_start or not range_end:
            continue
        intervals.append((date.fromisoformat(range_start), date.fromisoformat(range_end)))
    if not intervals:
        return False, fecha_desde, fecha_hasta

    intervals.sort(key=lambda item: item[0])
    cursor = start
    for interval_start, interval_end in intervals:
        if interval_end < cursor:
            continue
        if interval_start > cursor:
            return False, cursor.isoformat(), min(interval_start - timedelta(days=1), end).isoformat()
        if interval_end >= cursor:
            cursor = interval_end + timedelta(days=1)
        if cursor > end:
            return True, None, None
    return False, cursor.isoformat(), end.isoformat()


def get_erp_prefilter_options() -> dict:
    db = get_db()
    if db is None:
        return {}
    _ensure_erp_indexes(db)

    articles = list(
        db[ERP_ARTICLES_COLLECTION].find(
            {},
            {
                "_id": 0,
                "family": 1,
                "line": 1,
                "brand": 1,
                "business_unit": 1,
                "supplier": 1,
            },
        )
    )
    sellers = list(
        db[ERP_SELLERS_COLLECTION].find(
            {},
            {
                "_id": 0,
                "sales_force": 1,
                "seller_name": 1,
            },
        )
    )
    routes = list(
        db[ERP_ROUTES_COLLECTION].find(
            {},
            {
                "_id": 0,
                "route_description": 1,
            },
        )
    )

    labels = {
        "family": "Familia",
        "line": "Línea",
        "brand": "Marca",
        "business_unit": "Unidad de negocio",
        "supplier": "Proveedor",
        "sales_force": "Fuerza de ventas",
        "route_description": "Ruta",
        "seller_name": "Vendedor",
    }
    docs_by_field = {
        "family": articles,
        "line": articles,
        "brand": articles,
        "business_unit": articles,
        "supplier": articles,
        "sales_force": sellers,
        "route_description": routes,
        "seller_name": sellers,
    }
    options = {}
    for field, docs in docs_by_field.items():
        counter = Counter()
        for doc in docs:
            value = str(doc.get(field) or "").strip()
            if value:
                counter[value] += 1
        if not counter:
            continue
        options[field] = {
            "label": labels[field],
            "kind": "text",
            "options": [
                {
                    "value": value,
                    "label": value,
                    "count": counter[value],
                }
                for value in sorted(counter.keys(), key=lambda item: item.lower())
            ],
        }
    return options


def sync_erp_articles(records: list[dict], origin: str = "manual") -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("MongoDB no está configurado")
    _ensure_erp_indexes(db)

    operations = []
    row_versions = 0
    for record in records:
        document = dict(record)
        document["_id"] = document.get("product_key")
        document["storedAt"] = datetime.now(timezone.utc)
        if record.get("row_version") is not None:
            row_versions += 1
        operations.append(
            UpdateOne(
                {"_id": document["_id"]},
                {"$set": document},
                upsert=True,
            )
        )

    result = db[ERP_ARTICLES_COLLECTION].bulk_write(operations, ordered=False) if operations else None
    summary = {
        "recordsReceived": len(records),
        "rowVersions": row_versions,
        "upserted": result.upserted_count if result else 0,
        "modified": result.modified_count if result else 0,
        "matched": result.matched_count if result else 0,
        "timestamp": datetime.now(timezone.utc),
        "origin": origin,
    }
    db[ERP_SYNC_COLLECTION].insert_one(
        {
            **summary,
            "entity": "articles",
        }
    )
    summary["timestamp"] = summary["timestamp"].isoformat()
    return summary


def sync_erp_sellers(records: list[dict], origin: str = "manual") -> dict:
    return _sync_simple_master(
        ERP_SELLERS_COLLECTION,
        records,
        "sellers",
        origin,
        lambda item: item.get("seller_key") or item.get("seller_name"),
        replace_all=True,
    )


def sync_erp_routes(records: list[dict], origin: str = "manual") -> dict:
    return _sync_simple_master(
        ERP_ROUTES_COLLECTION,
        records,
        "routes",
        origin,
        lambda item: "|".join(
            [
                str(item.get("branch_key") or ""),
                str(item.get("route_key") or ""),
                str(item.get("seller_key") or ""),
            ]
        ) or item.get("route_description") or item.get("seller_name"),
        replace_all=True,
    )


def sync_erp_marketing(records: list[dict], origin: str = "manual") -> dict:
    return _sync_simple_master(
        ERP_MARKETING_COLLECTION,
        records,
        "marketing",
        origin,
        lambda item: item.get("marketing_key"),
        replace_all=True,
    )


def _sync_simple_master(collection_name: str, records: list[dict], entity: str, origin: str, key_fn, replace_all: bool = False) -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("MongoDB no está configurado")
    _ensure_erp_indexes(db)

    deleted = 0
    if replace_all:
        deleted = db[collection_name].delete_many({}).deleted_count
    operations = []
    for record in records:
        document = dict(record)
        document["_id"] = key_fn(record)
        if not document["_id"]:
            continue
        document["storedAt"] = datetime.now(timezone.utc)
        operations.append(
            UpdateOne(
                {"_id": document["_id"]},
                {"$set": document},
                upsert=True,
            )
        )

    result = db[collection_name].bulk_write(operations, ordered=False) if operations else None
    summary = {
        "recordsReceived": len(records),
        "deleted": deleted,
        "upserted": result.upserted_count if result else 0,
        "modified": result.modified_count if result else 0,
        "matched": result.matched_count if result else 0,
        "timestamp": datetime.now(timezone.utc),
        "origin": origin,
    }
    db[ERP_SYNC_COLLECTION].insert_one({**summary, "entity": entity})
    summary["timestamp"] = summary["timestamp"].isoformat()
    return summary


def load_erp_articles_dataset() -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("MongoDB no está configurado")
    _ensure_erp_indexes(db)

    cursor = db[ERP_ARTICLES_COLLECTION].find({}, {"storedAt": 0}).sort("product_key", ASCENDING)
    records = list(cursor)
    if not records:
        raise ValueError("MongoDB no tiene artículos ERP persistidos")

    for record in records:
        record.pop("_id", None)

    return {
        **build_dataset("articles", "MongoDB ERP artículos", "MongoDB erp_articles", records, source_kind="mongo"),
    }


def load_erp_sellers_dataset() -> dict:
    return _load_simple_master_dataset(
        ERP_SELLERS_COLLECTION,
        "seller_name",
        "sellers",
        "MongoDB ERP vendedores",
        "MongoDB erp_sellers",
    )


def load_erp_routes_dataset() -> dict:
    return _load_simple_master_dataset(
        ERP_ROUTES_COLLECTION,
        "route_description",
        "routes",
        "MongoDB ERP rutas",
        "MongoDB erp_routes",
    )


def load_erp_marketing_dataset() -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("MongoDB no está configurado")
    _ensure_erp_indexes(db)
    cursor = db[ERP_MARKETING_COLLECTION].find({}, {"storedAt": 0}).sort(
        [("segment_name", ASCENDING), ("channel_name", ASCENDING), ("subchannel_name", ASCENDING)]
    )
    records = list(cursor)
    if not records:
        raise ValueError("MongoDB no tiene jerarquía de marketing ERP persistida")
    for record in records:
        record.pop("_id", None)
    return {
        "datasetType": "marketing",
        "sourceKind": "mongo",
        "file": "MongoDB ERP marketing",
        "sheet": "MongoDB erp_marketing",
        "headerRow": 0,
        "rowsRead": len(records),
        "rowsValid": len(records),
        "headers": [],
        "mapping": {},
        "records": records,
    }


def _load_simple_master_dataset(collection_name: str, sort_key: str, dataset_type: str, file_label: str, sheet_label: str) -> dict:
    db = get_db()
    if db is None:
        raise RuntimeError("MongoDB no está configurado")
    _ensure_erp_indexes(db)
    cursor = db[collection_name].find({}, {"storedAt": 0}).sort(sort_key, ASCENDING)
    records = list(cursor)
    if not records:
        raise ValueError(f"MongoDB no tiene {dataset_type} ERP persistidos")
    for record in records:
        record.pop("_id", None)
    return build_dataset(dataset_type, file_label, sheet_label, records, source_kind="mongo")


def get_erp_storage_status() -> dict:
    db = get_db()
    if db is None:
        return {
            "connected": False,
            "available": False,
            "message": "MongoDB no está configurado.",
        }
    try:
        _ensure_erp_indexes(db)
        total = db[ERP_SALES_COLLECTION].count_documents({})
        article_total = db[ERP_ARTICLES_COLLECTION].count_documents({})
        seller_total = db[ERP_SELLERS_COLLECTION].count_documents({})
        route_total = db[ERP_ROUTES_COLLECTION].count_documents({})
        marketing_total = db[ERP_MARKETING_COLLECTION].count_documents({})
        first = db[ERP_SALES_COLLECTION].find_one({}, sort=[("date", ASCENDING)])
        last = db[ERP_SALES_COLLECTION].find_one({}, sort=[("date", DESCENDING)])
        sales_sync = db[ERP_SYNC_COLLECTION].find_one({"entity": "sales_batch"}, sort=[("timestamp", DESCENDING)])
        if sales_sync is None:
            sales_sync = db[ERP_SYNC_COLLECTION].find_one({"entity": "sales"}, sort=[("timestamp", DESCENDING)])
        article_sync = db[ERP_SYNC_COLLECTION].find_one({"entity": "articles"}, sort=[("timestamp", DESCENDING)])
        seller_sync = db[ERP_SYNC_COLLECTION].find_one({"entity": "sellers"}, sort=[("timestamp", DESCENDING)])
        route_sync = db[ERP_SYNC_COLLECTION].find_one({"entity": "routes"}, sort=[("timestamp", DESCENDING)])
        marketing_sync = db[ERP_SYNC_COLLECTION].find_one({"entity": "marketing"}, sort=[("timestamp", DESCENDING)])
        return {
            "connected": True,
            "available": total > 0,
            "message": "Ventas ERP persistidas en MongoDB." if total else "Todavía no hay ventas ERP guardadas en MongoDB.",
            "records": total,
            "periodStart": first.get("date") if first else None,
            "periodEnd": last.get("date") if last else None,
            "lastSyncAt": sales_sync.get("timestamp").isoformat() if sales_sync and sales_sync.get("timestamp") else None,
            "lastRange": sales_sync.get("range") if sales_sync else None,
            "lastChunkCount": sales_sync.get("chunkCount") if sales_sync else None,
            "lastSyncRowsValid": sales_sync.get("rowsValid") if sales_sync else None,
            "lastSyncWarning": sales_sync.get("warning") if sales_sync else None,
            "articleRecords": article_total,
            "articlesAvailable": article_total > 0,
            "articlesLastSyncAt": article_sync.get("timestamp").isoformat() if article_sync and article_sync.get("timestamp") else None,
            "sellerRecords": seller_total,
            "routeRecords": route_total,
            "marketingRecords": marketing_total,
            "sellersLastSyncAt": seller_sync.get("timestamp").isoformat() if seller_sync and seller_sync.get("timestamp") else None,
            "routesLastSyncAt": route_sync.get("timestamp").isoformat() if route_sync and route_sync.get("timestamp") else None,
            "marketingLastSyncAt": marketing_sync.get("timestamp").isoformat() if marketing_sync and marketing_sync.get("timestamp") else None,
        }
    except Exception as exc:
        return {
            "connected": True,
            "available": False,
            "message": f"No se pudo consultar el almacenamiento ERP en MongoDB: {exc}",
        }


def ping():
    """Comprueba la conexión con Atlas. Retorna True si está disponible."""
    db = get_db()
    if db is None:
        return False
    try:
        db.client.admin.command("ping")
        return True
    except ConnectionFailure:
        return False


def _ensure_non_unique_index(collection, name: str, keys, sparse: bool = False):
    existing = collection.index_information().get(name)
    if existing and not existing.get("unique"):
        return
    try:
        collection.drop_index(name)
    except Exception:
        pass
    collection.create_index(keys, sparse=sparse, name=name)


def save_session(datasets: dict) -> bool:
    """
    Persiste la configuración de datasets (archivos, hojas, mappings, headerRow)
    en la colección 'sessions'. Hace upsert sobre el id fijo.

    Args:
        datasets: dict con la estructura que maneja el frontend (sales.sources, etc.)

    Returns:
        True si se guardó, False si Mongo no está disponible.
    """
    db = get_db()
    if db is None:
        return False
    try:
        db["sessions"].update_one(
            {"_id": _SESSION_ID},
            {"$set": {"datasets": datasets}},
            upsert=True,
        )
        return True
    except Exception:
        return False


def load_session() -> dict | None:
    """
    Recupera la configuración de datasets guardada.

    Returns:
        dict con key 'datasets', o None si no hay sesión guardada o Mongo no está disponible.
    """
    db = get_db()
    if db is None:
        return None
    try:
        doc = db["sessions"].find_one({"_id": _SESSION_ID}, {"_id": 0, "datasets": 1})
        return doc if doc else None
    except Exception:
        return None
