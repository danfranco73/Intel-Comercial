from __future__ import annotations

from typing import Any, Callable
import json

from analyzer import (
    DATASET_DEFINITIONS,
    analyze_datasets,
    apply_filters,
    build_consistency_report,
    build_tactical_month_report,
    enrich_sales,
    load_dataset,
    load_sales_dataset,
    normalize_text,
)
from analysis_engine import AnalysisEngine
from insight_writer import insights_summary, write_insights
from kpi_generator import generate_kpis
from rule_engine import resolve_tasks
from schema_detector import detect_schema
from viz_selector import build_viz
from mongo_client import load_erp_sales_dataset
from sales_coach.schemas import AnalysisRequest


class AnalysisService:
    def __init__(self, dataset_resolver: Callable[[dict[str, Any]], dict[str, Any]]):
        self.dataset_resolver = dataset_resolver

    def analyze(self, payload: Any, scope_filters: dict[str, list[str]] | None):
        request = AnalysisRequest.parse(payload)
        resolved = self.dataset_resolver(request.datasets)
        return analyze_datasets(
            resolved,
            filters=request.filters,
            supplier_focus=request.supplier_focus,
            planning=request.planning,
            scope_filters=scope_filters,
        )

    def consistency(self, datasets: dict[str, Any], scope_filters):
        return build_consistency_report(
            self.dataset_resolver(datasets),
            scope_filters=scope_filters,
        )

    def dynamic(self, payload: Any, scope_filters):
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto JSON")
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("Falta task_id")
        datasets = payload.get("datasets")
        if not isinstance(datasets, dict) or not datasets:
            raise ValueError("No llegaron datasets para analizar")

        resolved = self.dataset_resolver(datasets)
        loaded = {"sales": load_sales_dataset(resolved["sales"])} if "sales" in resolved else {}
        for dataset_type, source in resolved.items():
            if dataset_type != "sales":
                loaded[dataset_type] = load_dataset(dataset_type, source)
        if "sales" not in loaded:
            raise ValueError("No se pudo resolver el dataset de ventas")

        sales_records = loaded["sales"]["records"]
        article_map = {
            row["product_key"]: row
            for row in loaded.get("articles", {}).get("records", [])
            if row.get("product_key")
        }
        route_seller_map = {
            normalize_text(row["seller_name"]): row
            for row in loaded.get("routes", {}).get("records", [])
            if row.get("seller_name")
        }
        seller_key_map = {
            row["seller_key"]: row
            for row in loaded.get("sellers", {}).get("records", [])
            if row.get("seller_key")
        }
        seller_name_map = {
            normalize_text(row["seller_name"]): row
            for row in loaded.get("sellers", {}).get("records", [])
            if row.get("seller_name")
        }
        seller_route_map = {
            normalize_text(row["route_description"]): row
            for row in loaded.get("sellers", {}).get("records", [])
            if row.get("route_description")
        }
        enriched = enrich_sales(
            sales_records,
            article_map,
            route_seller_map,
            seller_key_map,
            seller_name_map,
            seller_route_map,
        )
        _, scoped = apply_filters(enriched, scope_filters or {})
        _, filtered = apply_filters(scoped, payload.get("filters", {}))
        task = next(
            (item for item in resolve_tasks(detect_schema(filtered)) if item["id"] == task_id),
            None,
        )
        if task is None:
            raise ValueError(f"El análisis '{task_id}' no está disponible con los datos actuales")

        result = AnalysisEngine().run_task_with_combo(task, filtered, payload.get("combo"))
        kpi_set = generate_kpis({task_id: result}, [task])
        insights = write_insights(kpi_set, {task_id: result}, [task])
        return {
            "task_id": task_id,
            "combo": payload.get("combo"),
            "result": result,
            "kpiSet": kpi_set,
            "vizSpec": build_viz(task, result),
            "insights": insights,
            "insightsSummary": insights_summary(insights),
        }

    def tactical(self, payload: Any, scope_filters):
        request = AnalysisRequest.parse(payload)
        tactical_datasets = json.loads(json.dumps(request.datasets))
        if "sales" in tactical_datasets and isinstance(tactical_datasets["sales"], dict):
            sales_config = tactical_datasets["sales"]
            sales_config["loadStrategy"] = "tactical_month"
            sales_config["allowPartialCoverage"] = True
            sales_start = sales_config.get("fechaDesde") or (sales_config.get("erp") or {}).get("fechaDesde")
            sales_end = sales_config.get("fechaHasta") or (sales_config.get("erp") or {}).get("fechaHasta")
            if sales_config.get("source") == "auto" and sales_start and sales_end:
                try:
                    load_erp_sales_dataset(sales_start, sales_end, require_coverage=True)
                    sales_config["source"] = "mongo"
                    if isinstance(sales_config.get("erp"), dict):
                        sales_config["erp"]["enabled"] = False
                except Exception:
                    pass
        return build_tactical_month_report(
            self.dataset_resolver(tactical_datasets),
            filters=request.filters,
            supplier_focus=request.supplier_focus,
            planning=request.planning,
            scope_filters=scope_filters,
        )
