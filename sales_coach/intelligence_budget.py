"""Conservative admission limits for the legacy Intelligence calculations only.

Limits reject a whole request; they never certify a truncated report. No retries,
DDL or permission changes. This is not a process-wide RSS limit.
"""
from contextvars import ContextVar
from functools import wraps
from threading import BoundedSemaphore
import sys
import time

from pymongo import timeout
from sales_coach.concurrency import heavy_analysis_slot, AnalysisCapacityExceeded

MAX_ROWS = 5000
MAX_TOTAL_ROWS = 20000
MAX_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES = 256 * 1024
SECONDS = 5
_active = ContextVar('intelligence_budget', default=None)
_slot = BoundedSemaphore(1)


class IntelligenceCapacityExceeded(RuntimeError):
    pass


def current_budget():
    return _active.get()


class Budget:
    def __init__(self):
        self.deadline = time.monotonic() + SECONDS
        self.rows = self.bytes = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise IntelligenceCapacityExceeded('deadline')

    def consume(self, value):
        self.check()
        self.rows += 1
        if self.rows > MAX_TOTAL_ROWS:
            raise IntelligenceCapacityExceeded('rows')
        size = 0
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            size += sys.getsizeof(item)
            if depth > 12 or size > MAX_DOCUMENT_BYTES:
                raise IntelligenceCapacityExceeded('document')
            if isinstance(item, dict):
                if len(item) > 1024:
                    raise IntelligenceCapacityExceeded('document')
                pending.extend((v, depth + 1) for pair in item.items() for v in pair)
            elif isinstance(item, (list, tuple)):
                if len(item) > 1024:
                    raise IntelligenceCapacityExceeded('document')
                pending.extend((v, depth + 1) for v in item)
        self.bytes += size
        if self.bytes > MAX_BYTES:
            raise IntelligenceCapacityExceeded('bytes')


def bounded_calculation(function):
    @wraps(function)
    def run(*args, **kwargs):
        if current_budget() is not None:
            return function(*args, **kwargs)
        if not _slot.acquire(blocking=False):
            raise IntelligenceCapacityExceeded('busy')
        budget = Budget()
        token = _active.set(budget)
        try:
            # CSOT includes Mongo cursor getMore, not just the initial query.
            with heavy_analysis_slot(timeout=0), timeout(SECONDS):
                result = function(*args, **kwargs)
                budget.check()
                return result
        except AnalysisCapacityExceeded as exc:
            raise IntelligenceCapacityExceeded("busy") from exc
        finally:
            _active.reset(token)
            _slot.release()
    return run


def bounded_rows(cursor):
    budget = current_budget()
    try:
        for index, row in enumerate(cursor):
            if budget is not None:
                if index >= MAX_ROWS:
                    raise IntelligenceCapacityExceeded('rows')
                budget.consume(row)
            yield row
    finally:
        cursor.close()


class ReadCollection:
    def __init__(self, collection):
        self.collection = collection

    def find(self, *args, **kwargs):
        cursor = self.collection.find(*args, **kwargs)
        if current_budget() is None:
            return cursor
        cursor = cursor.limit(MAX_ROWS + 1).batch_size(1).max_time_ms(2000)
        return bounded_rows(cursor)

    def find_one(self, *args, **kwargs):
        if current_budget() is None:
            return self.collection.find_one(*args, **kwargs)
        # Use an actual cursor so CSOT, batch size and close apply to large docs.
        cursor = self.collection.find(*args, **kwargs).limit(1).batch_size(1).max_time_ms(2000)
        rows = bounded_rows(cursor)
        try:
            return next(rows, None)
        finally:
            rows.close()

    def aggregate(self, pipeline, **kwargs):
        if current_budget() is None:
            return self.collection.aggregate(pipeline, **kwargs)
        cursor = self.collection.aggregate([*pipeline, {'$limit': MAX_ROWS + 1}],
            maxTimeMS=2000, allowDiskUse=False, batchSize=1)
        return bounded_rows(cursor)


class ReadDatabase:
    def __init__(self, db):
        self.db = db.db if isinstance(db, ReadDatabase) else db

    def __getitem__(self, key):
        return ReadCollection(self.db[key])


def clickhouse_settings():
    return dict(max_execution_time=2, timeout_overflow_mode='throw',
                max_memory_usage=128 * 1024 * 1024,
                max_rows_to_read=2000000, max_bytes_to_read=128 * 1024 * 1024,
                read_overflow_mode='throw', max_result_rows=MAX_ROWS + 1,
                max_result_bytes=4 * 1024 * 1024, result_overflow_mode='throw',
                max_block_size=128, max_threads=1)
