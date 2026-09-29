from uuid import uuid4
from pymongo.errors import BulkWriteError
from sales_coach.domain.intelligence import now_iso


class StockSnapshotRepository:
    def __init__(self, db):
        self.headers = db["intelligence_stock_snapshots"]
        self.rows = db["intelligence_stock_rows"]
        self.rejections = db["intelligence_stock_rejections"]

    def start(self, configuration, stock_date, actor, identity_version=None):
        eligible = [d for d in configuration["deposits"] if d.get("configured") and d.get("active") is True
                    and d.get("include_in_stock_analysis") is True]
        header = {"snapshot_id": uuid4().hex, "schema_version": "1.0", "started_at": now_iso(),
                  "finished_at": None, "requested_stock_date": stock_date, "actor": actor,
                  "status": "running", "source": "ChessERP/stock", "frescura": True,
                  "configuration": configuration, "expected_deposit_ids": [d["deposit_id"] for d in eligible],
                  "execution_coverage": "running", "deposits": [], "identity_version": identity_version}
        self.headers.insert_one(dict(header))
        return header

    def progress(self, snapshot_id, deposits):
        self.headers.update_one({"snapshot_id": snapshot_id}, {"$set": {"deposits": deposits}})

    def store_rows(self, snapshot_id, deposit_id, rows, captured_at):
        # Position, not business dimensions, preserves repeated/unknown lot rows.
        documents = [{**row, "_id": f"{snapshot_id}:{deposit_id}:{index}", "snapshot_id": snapshot_id,
                      "deposit_id": deposit_id, "ordinal": index, "captured_at": captured_at}
                     for index, row in enumerate(rows)]
        for offset in range(0, len(documents), 500):
            try:
                self.rows.insert_many(documents[offset:offset + 500], ordered=False)
            except BulkWriteError as exc:
                if exc.details.get("writeConcernErrors") or any(e.get("code") != 11000 for e in exc.details.get("writeErrors", [])):
                    raise

    def reject(self, snapshot_id, deposit_id, ordinal, raw):
        self.rejections.update_one({"_id": f"{snapshot_id}:{deposit_id}:{ordinal}"}, {"$setOnInsert": {
            "snapshot_id": snapshot_id, "deposit_id": deposit_id, "ordinal": ordinal,
            "raw": raw, "error_code": "INVALID_STOCK_ROW", "captured_at": now_iso()}}, upsert=True)

    def finish(self, header):
        results = header["deposits"]
        complete = bool(header["expected_deposit_ids"]) and len(results) == len(header["expected_deposit_ids"]) and all(
            d["coverage_status"] == "complete" for d in results)
        status = "complete" if complete else "partial" if any(d.get("rows_valid", 0) for d in results) else "unavailable"
        header.update(status=status, execution_coverage="complete" if complete else "incomplete",
                      finished_at=now_iso())
        self.headers.update_one({"snapshot_id": header["snapshot_id"]}, {"$set": header})
        return header

    def select(self, snapshot_id=None):
        if snapshot_id:
            return self.headers.find_one({"snapshot_id": snapshot_id}, {"_id": 0})
        return (self.headers.find_one({"status": "complete"}, {"_id": 0}, sort=[("finished_at", -1)])
                or self.headers.find_one({"status": {"$ne": "running"}}, {"_id": 0}, sort=[("finished_at", -1)]))
