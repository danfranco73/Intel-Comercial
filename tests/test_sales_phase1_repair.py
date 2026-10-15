"""Safety/contract tests only. Business reconciliation uses actual isolated DBs."""
from copy import deepcopy
from datetime import date

import pytest

from bi.facts import enrich_sales_records
from erp_client import normalize_erp_sale_row
from sales_coach.services import sales_phase1_repair as repair
from scripts.analyze_sales_phase1 import KEY
from scripts.audit_sales_phase1 import row_fingerprint
from tests.test_sales_phase1_audit import complete_source, persisted


@pytest.mark.parametrize("final,net,tax", [(100.4,80,3.6), (0,0,0), (-100.4,-80,-3.6)])
def test_explicit_source_money_and_legacy_alias(final, net, tax):
    raw = complete_source(subtotalFinal=final, subtotalNeto=net, internos=tax,
                          subtotalImpuestosInternos=999)
    row = normalize_erp_sale_row(raw)
    assert row["amount_final"] == row["amount"] == final
    assert row["internal_taxes"] == tax
    assert row["amount_net_internal"] == net + tax
    enriched = enrich_sales_records([row], {})[0]
    assert enriched["amount_final"] == enriched["amount_gross"] == final
    assert enriched["amount"] == net
    again = enrich_sales_records([enriched], {})[0]
    assert again["amount_final"] == final
    assert again["amount_gross"] == final
    assert again["amount"] == net


def test_legacy_enriched_payload_keeps_final_when_enriched_again():
    row = persisted(complete_source(subtotalNeto=80, subtotalFinal=100.4))
    row.pop("amount_final")
    row.update(amount=80, amount_gross=100.4)
    result = enrich_sales_records([row], {})[0]
    assert result["amount_final"] == result["amount_gross"] == 100.4
    assert result["amount"] == 80


class MemoryStore:
    # Explicit test double, never evidence of a database gate.
    name = "mongo"

    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.calls = 0

    def read(self):
        return deepcopy(self.rows)

    def write(self, pairs):
        self.calls += 1
        by_key = {repair.key(r): r for r in self.rows}
        for before, after, fields in pairs:
            row = by_key[repair.key(before)]
            for field in fields:
                row[field] = after[field]


def sample(monkeypatch):
    monkeypatch.setattr(repair, "EXPECTED_ROWS", 1)
    row = persisted(complete_source(subtotalNeto=80, subtotalFinal=100.4, internos=3.6))
    row.update(_id="test", date="2026-09-22", internal_taxes=0)
    change = {"key": {f: str(row.get(f) or "") for f in KEY},
              "before": {f:row[f] for f in repair.PATCH_FIELDS},
              "after": {"internal_taxes": "3.6", "amount_net_internal": "83.6"},
              "before_economic_fingerprint": row_fingerprint([row])}
    return row, change


@pytest.mark.parametrize("corruption", ["duplicate", "period", "field", "precondition"])
def test_invalid_plan_rejected_before_writes(monkeypatch, corruption):
    row, change = sample(monkeypatch)
    plan = [change]
    if corruption == "duplicate":
        plan.append(deepcopy(change))
    elif corruption == "period":
        change["key"]["date"] = "2026-09-29"
    elif corruption == "field":
        change["after"]["quantity"] = 999
    else:
        row["amount"] += 1
    with pytest.raises(ValueError):
        repair.expected_rows([row], plan)


def test_restart_rollback_restores_bson_numeric_type_and_is_idempotent(monkeypatch, tmp_path):
    from bson import json_util
    import gzip
    row, change = sample(monkeypatch)
    store = MemoryStore([row])
    directory = tmp_path / "executor"
    executor = repair.RepairExecutor([store], {"mongo": [row]}, [change], directory)
    with pytest.raises(RuntimeError, match="Injected interruption"):
        executor.execute("apply", fail_after_batches=1)
    assert store.rows[0]["internal_taxes"] == 3.6
    executor.close()
    backup = [json_util.loads(s) for s in gzip.open(directory / "mongo-backup.jsonl.gz", "rt")]
    resumed = repair.RepairExecutor([store], {"mongo": backup}, [change], directory, resume=True)
    resumed.execute("rollback")
    assert store.rows == [row]
    assert type(store.rows[0]["internal_taxes"]) is int
    assert resumed.execute("rollback")["mongo"]["written_rows"] == 0
    resumed.close()


def test_external_change_blocks_rollback_without_overwriting(monkeypatch, tmp_path):
    row, change = sample(monkeypatch)
    store = MemoryStore([row])
    executor = repair.RepairExecutor([store], {"mongo": [row]}, [change], tmp_path / "executor")
    executor.execute("apply")
    store.rows[0]["quantity"] += 1
    calls = store.calls
    with pytest.raises(ValueError, match="external changes"):
        executor.execute("rollback")
    assert store.calls == calls
    executor.close()


def test_repeated_apply_and_concurrent_executor_rejected(monkeypatch, tmp_path):
    row, change = sample(monkeypatch)
    store = MemoryStore([row])
    directory = tmp_path / "executor"
    executor = repair.RepairExecutor([store], {"mongo": [row]}, [change], directory)
    with pytest.raises(BlockingIOError):
        repair.RepairExecutor([store], {"mongo": [row]}, [change], directory, resume=True)
    executor.execute("apply")
    with pytest.raises(ValueError, match="cannot be resumed"):
        executor.execute("apply")
    executor.close()


def test_sql_write_keeps_period_and_before_values_in_predicate(monkeypatch):
    row, change = sample(monkeypatch)
    class Client:
        def command(self, sql, settings):
            self.sql, self.settings = sql, settings
    client = Client()
    after = repair.expected_rows([row], [change])[0]
    repair.ClickHouseStore(client, "isolated.sales").write([(row, after, tuple(change["after"]))])
    assert "date >= '2026-09-22' AND date <= '2026-09-28'" in client.sql
    assert ",amount,amount_net,amount_final,internal_taxes,amount_net_internal) IN (tuple(" in client.sql
    assert ",100.4,80.0,100.4,0.0,80.0)" in client.sql
    assert client.settings["mutations_sync"] == 2
    with pytest.raises(ValueError, match="Unsafe table"):
        repair.ClickHouseStore(client, "sales; DROP TABLE sales")


def test_mongo_compare_and_set_preserves_absent_dimension(monkeypatch):
    import mongomock
    from types import SimpleNamespace
    row, change = sample(monkeypatch)
    row.pop("seller_key", None)
    collection = mongomock.MongoClient().isolated.sales
    collection.insert_one(row)
    # mongomock's bulk adapter does not support PyMongo's newer sort argument;
    # exercise these exact predicates via its update_one evaluator instead.
    def bulk(operations, ordered):
        return SimpleNamespace(matched_count=sum(collection.update_one(op._filter, op._doc, upsert=op._upsert).matched_count
                                                 for op in operations))
    monkeypatch.setattr(collection, "bulk_write", bulk)
    after = deepcopy(row)
    after["internal_taxes"] = 3.6
    repair.MongoStore(collection).write([(row, after, ("internal_taxes",))])
    restored = collection.find_one({"_id": row["_id"]})
    assert restored["internal_taxes"] == 3.6
    assert "seller_key" not in restored
