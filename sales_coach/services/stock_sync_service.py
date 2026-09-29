from datetime import date
from time import monotonic
from uuid import uuid4
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from datetime import datetime, timedelta, timezone

from erp_client import fetch_stock_dataset
from sales_coach.domain.intelligence import now_iso
from sales_coach.domain.stock import normalize_stock_row
from sales_coach.repositories.deposit_repository import DepositRepository
from sales_coach.repositories.stock_snapshot_repository import StockSnapshotRepository
from sales_coach.repositories.product_identity_repository import ProductIdentityRepository


class StockSyncService:
    def __init__(self, db, fetcher=None):
        self.db = db
        self.fetcher = fetcher or fetch_stock_dataset
        self.repository = StockSnapshotRepository(db)

    def run(self, stock_date, actor="cli"):
        stock_date = date.fromisoformat(str(stock_date)).isoformat()
        from zoneinfo import ZoneInfo
        if date.fromisoformat(stock_date) > datetime.now(ZoneInfo("America/Argentina/Cordoba")).date():
            raise ValueError("No se puede capturar stock futuro")
        owner = uuid4().hex
        leases = self.db["intelligence_stock_leases"]
        now = datetime.now(timezone.utc)
        try:
            lease = leases.find_one_and_update({"_id": "stock", "$or": [
                {"expires_at": {"$lte": now}}, {"owner": owner}]},
                {"$set": {"owner": owner, "expires_at": now + timedelta(hours=1)}},
                upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError as exc:
            raise ValueError("Ya hay una captura de stock en ejecución") from exc
        if not lease or lease.get("owner") != owner:
            raise ValueError("No se pudo adquirir exclusión de stock")
        header = None
        try:
            identities = ProductIdentityRepository(self.db)
            identity_version = identities.version()
            bindings = identities.bindings(identity_version)
            header = self.repository.start(DepositRepository(self.db).configuration(), stock_date, actor, identity_version)
            for deposit_id in header["expected_deposit_ids"]:
                updated = leases.update_one({"_id": "stock", "owner": owner}, {"$set": {
                    "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)}})
                if not updated.matched_count:
                    raise RuntimeError("Lease perdido")
                started = monotonic()
                item = {"deposit_id": deposit_id, "started_at": now_iso(), "finished_at": None,
                        "duration_ms": None, "rows_received": None, "rows_valid": 0,
                        "distinct_articles": None, "observed_warehouse_ids": [], "errors": [],
                        "coverage_status": "running", "attempts": 1}
                header["deposits"].append(item)
                self.repository.progress(header["snapshot_id"], header["deposits"])
                try:
                    dataset = self.fetcher(deposit_id, stock_date)
                    raw_rows = dataset["records"]
                    if not isinstance(raw_rows, list):
                        raise ValueError("Respuesta inválida")
                    item["rows_received"] = len(raw_rows)
                    rows = []
                    for ordinal, row in enumerate(raw_rows):
                        try:
                            normalized = normalize_stock_row(row, deposit_id)
                            normalized["source_ordinal"] = ordinal
                            normalized["product_identity"] = bindings.get(normalized["physical_article_id"],
                                {"version": identity_version, "status": "unknown", "statistical_article_ids": []})
                            rows.append(normalized)
                        except (ValueError, TypeError, OverflowError):
                            item["errors"].append({"code": "INVALID_STOCK_ROW", "ordinal": ordinal})
                            self.repository.reject(header["snapshot_id"], deposit_id, ordinal, row)
                    self.repository.store_rows(header["snapshot_id"], deposit_id, rows, now_iso())
                    item.update(rows_valid=len(rows), distinct_articles=len({r["physical_article_id"] for r in rows}),
                                observed_warehouse_ids=sorted({r["warehouse_id"] for r in rows if r["warehouse_id"] is not None}))
                    item["coverage_status"] = "partial" if item["errors"] else "complete" if rows else "empty_unconfirmed"
                except Exception as exc:
                    item["coverage_status"] = "failed"
                    item["errors"].append({"code": "STOCK_CAPTURE_FAILED", "error_type": type(exc).__name__})
                item.update(finished_at=now_iso(), duration_ms=round((monotonic() - started) * 1000))
                self.repository.progress(header["snapshot_id"], header["deposits"])
            return self.repository.finish(header)
        except Exception:
            if header:
                self.repository.finish(header)
            raise
        finally:
            leases.delete_one({"_id": "stock", "owner": owner})
