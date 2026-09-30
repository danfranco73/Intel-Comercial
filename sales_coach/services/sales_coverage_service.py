"""Read-only certification of ingestion at the selected destination.

Observed dates alone never certify a range. A completed source write must agree
with persisted row counts. Cross-destination reconciliation is separate evidence.
"""
from datetime import date, datetime, timedelta, timezone


def day(value):
    return value.date() if isinstance(value, datetime) else value if isinstance(value, date) else date.fromisoformat(value)


def stamp(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def date_ranges(days):
    result = []
    for value in sorted(set(days)):
        if result and day(result[-1]["end"]) + timedelta(days=1) == value:
            result[-1]["end"] = value.isoformat()
        else:
            result.append({"start": value.isoformat(), "end": value.isoformat()})
    return result


def calendar_days(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def observed_daily(db, source):
    if source == "clickhouse":
        from clickhouse_client import get_clickhouse_client, _qualified_table
        rows = get_clickhouse_client().query(
            f"SELECT date, count() FROM {_qualified_table()} GROUP BY date ORDER BY date").result_rows
        return {day(d): int(n) for d, n in rows}
    if source != "mongo":
        raise ValueError("Fuente de cobertura desconocida")
    return {day(r["_id"]): r["rows"] for r in db["erp_sales"].aggregate([
        {"$group": {"_id": "$date", "rows": {"$sum": 1}}}])}


class SalesCoverageService:
    def __init__(self, db, daily_loader=None):
        self.db = db
        self.daily_loader = daily_loader or observed_daily

    def assess(self, source, windows):
        counts = self.daily_loader(self.db, source)
        counts = {day(k): int(v) for k, v in counts.items()}
        runs = list(self.db["sync_runs"].find({"entity": "sales"}))
        batches = list(self.db["erp_sync_runs"].find({"entity": "sales_batch"}))
        active_scheduler = self.db["scheduler_leases"].find_one({"_id": "sales_scheduler",
            "expires_at": {"$gt": datetime.now(timezone.utc)}}) is not None
        candidates = []

        def add(bounds, expected, at, evidence_id, basis, empty=False):
            try:
                left, right = day(bounds["fechaDesde"]), day(bounds["fechaHasta"])
            except (KeyError, TypeError, ValueError):
                return
            if right < left or expected is None or (expected == 0 and not empty):
                return
            actual = sum(n for d, n in counts.items() if left <= d <= right)
            if actual != expected:
                return
            candidates.append({"left": left, "right": right, "at": stamp(at),
                "id": evidence_id, "basis": basis, "rows": actual})

        # This journal is written only after all chunk writes have returned.
        # Use each destination's acknowledged chunk, never the requested batch range.
        for batch in batches:
            for index, chunk in enumerate(batch.get("chunks") or []):
                if source == "mongo":
                    # Mongo chunk bounds can be clipped by retention. Its native
                    # sales journal below has the actual persisted bounds.
                    continue
                if chunk.get("warning") and not chunk.get("emptyConfirmed"):
                    continue
                add(chunk.get("range") or {}, chunk.get("clickhouseStored"), batch.get("timestamp"),
                    f"erp_sync_runs:{batch.get('_id')}:{index}", "completed_chunk_and_current_row_count",
                    chunk.get("emptyConfirmed") is True)
        if source == "mongo":
            for run in self.db["erp_sync_runs"].find({"entity": "sales", "status": "success"}):
                add(run.get("range") or {}, run.get("recordsStored", run.get("recordsReceived")),
                    run.get("timestamp"), f"erp_sync_runs:{run.get('_id')}",
                    "native_mongo_write_and_current_row_count", run.get("emptyConfirmed") is True)
        for run in runs:
            if run.get("status") == "success":
                add(run.get("range") or {}, (run.get("rows_stored") or {}).get(source), run.get("finished_at"),
                    f"sync_runs:{run.get('run_id')}", "completed_run_and_current_row_count")

        # Resolve the newest source evidence for each day. A warning is accepted
        # only when its own native write completed, with an expected retention split.
        accepted = []
        for candidate in candidates:
            blockers = []
            for run in runs:
                if run.get("status") not in {"failed", "running", "warning"}:
                    continue
                bounds = run.get("range") or {}
                try:
                    left, right = day(bounds["fechaDesde"]), day(bounds["fechaHasta"])
                except (KeyError, TypeError, ValueError):
                    continue
                if right < candidate["left"] or left > candidate["right"]:
                    continue
                activity = stamp(run.get("updated_at") or run.get("finished_at") or run.get("started_at"))
                if (run.get("status") != "running" or not active_scheduler) and (
                        run.get("updated_at") or run.get("finished_at") or run.get("started_at")) and candidate["at"] > activity:
                    # A later completed write supersedes old failed/orphan journals
                    # for its own range. This does not rewrite the old run status.
                    continue
                same_write = stamp(run.get("started_at")) <= candidate["at"] <= stamp(run.get("finished_at"))
                recon = run.get("reconciliation") or {}
                split = ((run.get("rows_stored") or {}).get("mongo", 0) <
                         (run.get("rows_stored") or {}).get("clickhouse", 0))
                native = candidate["basis"] != "completed_run_and_current_row_count"
                if run.get("status") == "warning" and same_write and native and split and recon.get("consistent") is False:
                    # The expected split must also appear in the completed batch.
                    batch_match = any(b.get("range") == run.get("range") and
                        stamp(run.get("started_at")) <= stamp(b.get("timestamp")) <= stamp(run.get("finished_at")) and
                        b.get("mongoStored") == (run.get("rows_stored") or {}).get("mongo") and
                        b.get("clickhouseStored") == (run.get("rows_stored") or {}).get("clickhouse") and
                        any(c.get("storageMode") == "clickhouse_only" for c in b.get("chunks", [])) for b in batches)
                    if batch_match:
                        continue
                blockers.append((left, right))
            accepted.append((candidate, blockers))

        certified = {}
        evidence = {}
        for candidate, blockers in sorted(accepted, key=lambda pair: pair[0]["at"]):
            for value in calendar_days(candidate["left"], candidate["right"]):
                if not any(a <= value <= b for a, b in blockers):
                    certified[value] = candidate["id"]
                    evidence[candidate["id"]] = candidate
        result = {}
        for name, (left, right) in windows.items():
            days = calendar_days(left, right)
            missing = [d for d in days if d not in certified]
            zeros = [d for d in days if d in certified and not counts.get(d)]
            ids = sorted({certified[d] for d in days if d in certified})
            result[name] = {"status": "unverified" if missing else "complete", "start": left.isoformat(),
                "end": right.isoformat(), "source": source, "basis": "destination_write_evidence_and_current_rows",
                "observed_rows": sum(counts.get(d, 0) for d in days),
                "observed_days": sum(bool(counts.get(d)) for d in days),
                "certified_days": len(days) - len(missing), "unverified_ranges": date_ranges(missing),
                "confirmed_no_rows_ranges": date_ranges(zeros), "evidence_ids": ids}
        return result
