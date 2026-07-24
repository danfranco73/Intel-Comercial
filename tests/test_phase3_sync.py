from __future__ import annotations

import mongomock
import pytest

import sales_coach.services.reconciliation_service as reconciliation_module
import sales_coach.services.sync_service as sync_module
from sales_coach.repositories.sync_repository import SyncRepository
from sales_coach.services.reconciliation_service import ReconciliationService
from sales_coach.services.sync_service import SyncService


def test_sync_repository_tracks_lifecycle_checkpoint_and_lease():
    db = mongomock.MongoClient().db
    repository = SyncRepository(db)
    date_range = {"fechaDesde": "2026-07-01", "fechaHasta": "2026-07-23"}

    run_id = repository.start(
        "sales", "chess", ["mongo", "clickhouse"], date_range, "test", "admin"
    )
    repository.save_checkpoint(
        "sales", status="running", run_id=run_id, date_range=date_range
    )
    finished = repository.finish(
        run_id,
        status="success",
        rows_read=10,
        rows_stored={"mongo": 8, "clickhouse": 8},
    )

    assert finished["duration_ms"] >= 0
    assert repository.list_recent(1)[0]["status"] == "success"
    assert db.sync_checkpoints.find_one({"job_key": "sales"})["run_id"] == run_id
    assert repository.acquire_lease("scheduler", "owner-1", 60) is True
    assert repository.acquire_lease("scheduler", "owner-2", 60) is False
    repository.release_lease("scheduler", "owner-1")
    assert repository.acquire_lease("scheduler", "owner-2", 60) is True


def test_reconciliation_detects_consistent_totals(monkeypatch):
    db = mongomock.MongoClient().db
    db.erp_sales.insert_many(
        [
            {"date": "2026-07-01", "amount_net": 100.0, "quantity": 2.0},
            {"date": "2026-07-02", "amount_net": 50.0, "quantity": 1.0},
        ]
    )
    monkeypatch.setattr(
        reconciliation_module,
        "get_clickhouse_sales_aggregate",
        lambda *_args: {
            "available": True,
            "records": 2,
            "amountNet": 150.0,
            "quantity": 3.0,
        },
    )

    result = ReconciliationService(db).reconcile_sales("2026-07-01", "2026-07-31")

    assert result["comparable"] is True
    assert result["consistent"] is True
    assert result["difference"] == {"records": 0, "amountNet": 0.0, "quantity": 0.0}


def test_reconciliation_exposes_divergence(monkeypatch):
    db = mongomock.MongoClient().db
    db.erp_sales.insert_one(
        {"date": "2026-07-01", "amount_net": 100.0, "quantity": 2.0}
    )
    monkeypatch.setattr(
        reconciliation_module,
        "get_clickhouse_sales_aggregate",
        lambda *_args: {
            "available": True,
            "records": 2,
            "amountNet": 125.0,
            "quantity": 4.0,
        },
    )

    result = ReconciliationService(db).reconcile_sales("2026-07-01", "2026-07-31")

    assert result["consistent"] is False
    assert result["difference"]["records"] == -1
    assert result["difference"]["amountNet"] == -25.0


def test_sync_service_records_success_and_reconciliation(monkeypatch):
    db = mongomock.MongoClient().db
    monkeypatch.setattr(sync_module, "get_db", lambda: db)
    monkeypatch.setattr(sync_module, "erp_login", lambda: {"cookie": "test"})
    monkeypatch.setattr(
        sync_module,
        "get_erp_storage_status",
        lambda: {"available": True, "articlesAvailable": True},
    )
    monkeypatch.setattr(
        sync_module,
        "get_clickhouse_storage_status",
        lambda: {"configured": True, "available": True},
    )
    monkeypatch.setattr(
        sync_module.ReconciliationService,
        "reconcile_sales",
        lambda *_args: {"comparable": True, "consistent": True},
    )
    service = SyncService(
        lambda *_args, **_kwargs: {
            "rowsRead": 12,
            "rowsValid": 10,
            "mongoStored": 9,
            "clickhouseStored": 9,
        },
        lambda _storage: True,
    )

    result = service.run(
        {
            "fechaDesde": "2026-07-01",
            "fechaHasta": "2026-07-23",
            "refreshMasters": False,
        },
        requested_by="admin",
    )

    assert result["runId"]
    assert result["reconciliation"]["consistent"] is True
    assert db.sync_runs.find_one({"run_id": result["runId"]})["status"] == "success"


def test_sync_service_records_failure_and_checkpoint(monkeypatch):
    db = mongomock.MongoClient().db
    monkeypatch.setattr(sync_module, "get_db", lambda: db)
    monkeypatch.setattr(sync_module, "erp_login", lambda: {"cookie": "test"})
    service = SyncService(
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Chess caído")),
        lambda _storage: True,
    )

    with pytest.raises(RuntimeError, match="Chess caído"):
        service.run(
            {"fechaDesde": "2026-07-01", "fechaHasta": "2026-07-23"},
            requested_by="admin",
        )

    assert db.sync_runs.find_one()["status"] == "failed"
    assert db.sync_checkpoints.find_one({"job_key": "sales"})["status"] == "failed"
