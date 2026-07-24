from __future__ import annotations

from typing import Any, Callable

from clickhouse_client import get_clickhouse_storage_status
from erp_client import (
    erp_login,
    fetch_articles_dataset,
    fetch_marketing_dataset,
    fetch_routes_dataset,
    fetch_staff_dataset,
)
from mongo_client import (
    get_db,
    get_erp_storage_status,
    sync_erp_articles,
    sync_erp_marketing,
    sync_erp_routes,
    sync_erp_sellers,
)
from sales_coach.repositories.sync_repository import SyncRepository
from sales_coach.schemas import SyncRequest
from sales_coach.services.reconciliation_service import ReconciliationService


class SyncService:
    def __init__(
        self,
        sync_sales_range: Callable[..., dict[str, Any]],
        masters_available: Callable[[dict[str, Any]], bool],
    ):
        self.sync_sales_range = sync_sales_range
        self.masters_available = masters_available

    def run(
        self,
        payload: Any,
        *,
        requested_by: str | None = None,
        origin: str = "api",
    ) -> dict[str, Any]:
        request = SyncRequest.parse(payload)
        db = get_db()
        repository = SyncRepository(db)
        date_range = {
            "fechaDesde": request.fecha_desde,
            "fechaHasta": request.fecha_hasta,
        }
        run_id = repository.start(
            "sales",
            "chess",
            ["mongo", "clickhouse"],
            date_range,
            origin,
            requested_by,
        )
        repository.save_checkpoint(
            "sales",
            status="running",
            run_id=run_id,
            date_range=date_range,
        )
        try:
            result = self._execute(request, origin)
            reconciliation = ReconciliationService(db).reconcile_sales(
                request.fecha_desde,
                request.fecha_hasta,
            )
            status = "success" if reconciliation.get("consistent") is not False else "warning"
            repository.finish(
                run_id,
                status=status,
                rows_read=result["rowsRead"],
                rows_stored={
                    "mongo": result["mongoStored"],
                    "clickhouse": result["clickhouseStored"],
                },
                reconciliation=reconciliation,
                details={"mastersSynced": result["mastersSynced"]},
            )
            repository.save_checkpoint(
                "sales",
                status=status,
                run_id=run_id,
                date_range=date_range,
            )
            from sales_coach.services.alert_service import AlertService

            alert_summary = AlertService(db).generate_operational(
                actor_id=requested_by or "system"
            )
            return {
                **result,
                "runId": run_id,
                "reconciliation": reconciliation,
                "operationalAlerts": alert_summary,
            }
        except Exception as exc:
            repository.finish(run_id, status="failed", error=str(exc))
            repository.save_checkpoint(
                "sales",
                status="failed",
                run_id=run_id,
                date_range=date_range,
                error=str(exc),
            )
            raise

    def _execute(self, request: SyncRequest, origin: str) -> dict[str, Any]:
        session = erp_login()
        cookie = session.get("cookie")
        sync_summary = self.sync_sales_range(
            request.fecha_desde,
            request.fecha_hasta,
            cookie=cookie,
            force_refresh=request.force_refresh_sales,
        )
        storage = get_erp_storage_status()
        should_sync_masters = request.refresh_masters or not self.masters_available(storage)
        articles = sellers = routes = marketing = None
        article_summary = seller_summary = route_summary = marketing_summary = None
        if should_sync_masters:
            articles = fetch_articles_dataset(cookie=cookie)
            sellers = fetch_staff_dataset(cookie=cookie)
            routes = fetch_routes_dataset(cookie=cookie)
            marketing = fetch_marketing_dataset(cookie=cookie)
            sync_origin = f"{origin}_sync"
            article_summary = sync_erp_articles(articles["records"], origin=sync_origin)
            seller_summary = sync_erp_sellers(sellers["records"], origin=sync_origin)
            route_summary = sync_erp_routes(routes["records"], origin=sync_origin)
            marketing_summary = sync_erp_marketing(marketing["records"], origin=sync_origin)
            storage = get_erp_storage_status()
        return {
            "sync": sync_summary,
            "articlesSync": article_summary,
            "sellersSync": seller_summary,
            "routesSync": route_summary,
            "marketingSync": marketing_summary,
            "storage": storage,
            "clickhouseStorage": get_clickhouse_storage_status(),
            "rowsRead": sync_summary.get("rowsRead", 0),
            "rowsValid": sync_summary.get("rowsValid", 0),
            "mongoStored": sync_summary.get("mongoStored", 0),
            "clickhouseStored": sync_summary.get("clickhouseStored", 0),
            "warning": sync_summary.get("warning"),
            "warnings": sync_summary.get("warnings") or [],
            "mastersSynced": should_sync_masters,
            "forceRefreshSales": request.force_refresh_sales,
            "articleRowsRead": articles["rowsRead"] if articles else 0,
            "articleRowsValid": articles["rowsValid"] if articles else 0,
            "sellerRowsValid": sellers["rowsValid"] if sellers else 0,
            "routeRowsValid": routes["rowsValid"] if routes else 0,
            "marketingRowsValid": marketing["rowsValid"] if marketing else 0,
        }
