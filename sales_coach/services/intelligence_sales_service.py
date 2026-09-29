from collections import defaultdict
from calendar import monthrange
from decimal import Decimal

from analyzer import analyze_datasets
from bi.tactical_month import build_tactical_month_dashboard, _sum_amount, _sum_quantity, _pct_change
from sales_coach.domain.intelligence import envelope, signal
from sales_coach.schemas.intelligence import DIMENSIONS, HIERARCHY, TIMEZONE, sales_request
from sales_coach.services.intelligence_context_service import IntelligenceContextService
from sales_coach.services.objective_service import ObjectiveService


class IntelligenceSalesService:
    def __init__(self, db, context_service=None):
        self.db = db
        self.context_service = context_service or IntelligenceContextService(db)

    @staticmethod
    def _slice(context, name):
        bounds = context["coverage"][name]
        return [r for r in context["records"] if bounds["start"] <= r["date"].isoformat() <= bounds["end"]]

    @staticmethod
    def _comparison(current, previous, complete):
        if not complete:
            return {"current": None, "previous": None, "delta": None, "delta_pct": None, "status": "unverified"}
        return {"current": current, "previous": previous, "delta": round(current - previous, 2),
                "delta_pct": _pct_change(current, previous) if previous else None,
                "status": "comparable" if previous else "zero_baseline"}

    def brief(self, query, user):
        request = sales_request(query)
        context = self.context_service.build(request, user)
        end, windows = request["as_of"], context["windows"]
        result = envelope("sales", {"start": windows["currentStart"].isoformat(), "as_of": end.isoformat(),
            "month_end": windows["currentFullEnd"].isoformat() if "currentFullEnd" in windows else
                end.replace(day=monthrange(end.year, end.month)[1]).isoformat(),
            "timezone": TIMEZONE, "cutoff_basis": "requested_day"})
        complete = context["coverage"]["current"]["status"] == "complete"
        current = self._slice(context, "current")
        # This is the existing engine, fed an already strictly scoped context.
        tactical = build_tactical_month_dashboard(context["records"], end)
        summary = tactical["summary"]
        result["context_id"] = context["context_id"]
        result["scope"] = {"filters": request["filters"], "seller_attribution": "current_portfolio"}
        result["summary"] = {
            "net_sales": {"value": summary["salesMTD"] if complete else None, "metric_id": "net_sales",
                          "currency": None, "currency_status": "not_configured", "evidence_ids": [context["context_id"]]},
            "quantity": {"value": summary["unitsMTD"] if complete else None, "unit": "commercial_quantity",
                         "physical_unit_status": "unverified"},
            "active_clients": summary["activeClients"] if complete else None,
            "objective": None, "fulfillment_pct": None,
            "closing_projection": {"value": summary["projectedSalesClose"] if complete else None,
                "quantity": summary["projectedUnitsClose"] if complete else None,
                "method": "existing_calendar_linear", "seasonality_adjusted": False},
            "historical_benchmark": {"value": tactical["objective"]["salesClose"],
                "method": tactical["objective"]["method"], "is_approved_objective": False,
                "status": "unverified"}, "comparisons": []}
        # Benchmarks use full prior months, whose completeness is not implied by MTD coverage.
        if context["coverage"]["history"]["status"] == "complete":
            result["summary"]["historical_benchmark"]["status"] = "available"
        else:
            result["summary"]["historical_benchmark"]["value"] = None
        for name in ("mom", "yoy"):
            before = self._slice(context, name)
            valid = complete and context["coverage"][name]["status"] == "complete"
            comparison = {"id": name + "_mtd", **context["coverage"][name],
                "alignment": "same_calendar_day", "business_day_adjusted": False,
                "sales": self._comparison(_sum_amount(current), _sum_amount(before), valid),
                "quantity": self._comparison(_sum_quantity(current), _sum_quantity(before), valid)}
            result["summary"]["comparisons"].append(comparison)
        objectives = self._objectives(request, context, user, current) if complete else []
        result["summary"]["objectives"] = objectives
        sales_targets = [o for o in objectives if o["metric"] == "net_sales"]
        if len(sales_targets) == 1:
            result["summary"]["objective"] = sales_targets[0]
            result["summary"]["fulfillment_pct"] = sales_targets[0]["progress"]["fulfillment_pct"]
        breakdown = self._drilldown(request, context)
        result["drilldowns"] = breakdown
        result["deviations"] = [signal("sales", {"dimension": request["dimension"], "key": n["key"]},
            "net_sales", {"id": "mom_mtd", **n["sales"]}, [context["context_id"]])
            for n in breakdown["nodes"] if n["sales"].get("delta") is not None and n["sales"]["delta"] < 0]
        result["summary"]["budget"] = {"source": "user_preferences", "approved": False, "data": None}
        result["summary"]["opportunities"] = []
        result["legacy_alerts"] = []
        if context["records"] and context["coverage"]["history"]["status"] == "complete":
            datasets = {**context["loaded"], "sales": {"records": context["records"], "sourceKind": context["source"],
                "analysisRange": {"fechaDesde": windows["currentStart"].isoformat(), "fechaHasta": end.isoformat()},
                "comparisonRange": {"fechaDesde": windows["previousStart"].isoformat(), "fechaHasta": windows["previousEnd"].isoformat()}}}
            planning = (self.db["sessions"].find_one({"_id": user.id}, {"planning": 1}) or {}).get("planning")
            if current:
                report = analyze_datasets(datasets, planning=planning)
                result["explanations"] = [{"origin": "existing_deterministic_engine", "analytical_status": "legacy_rule",
                    "evidence_ids": [context["context_id"]], "content": item} for item in report.get("insights", [])]
                result["recommended_actions"] = [{"origin": "existing_deterministic_engine", "analytical_status": "legacy_rule",
                    "evidence_ids": [context["context_id"]], "action": item} for item in report.get("actionPlan", [])]
                result["summary"]["opportunities"] = report.get("opportunities", [])
                result["legacy_alerts"] = [{"origin": "existing_deterministic_engine",
                    "seasonality_calibrated": False, "evidence_ids": [context["context_id"]],
                    "alert": item} for item in report.get("alerts", [])]
                result["summary"]["budget"] = {"source": "user_preferences", "approved": False,
                    "data": report.get("dashboards", {}).get("history", {}).get("budget")}
        issues = [{"code": code} for code in context["issues"]]
        issues.extend({"code": "COVERAGE_UNVERIFIED", "window": name} for name, c in context["coverage"].items() if c["status"] != "complete")
        if not sales_targets:
            issues.append({"code": "APPLICABLE_SALES_OBJECTIVE_MISSING"})
        if len(sales_targets) > 1:
            issues.append({"code": "OVERLAPPING_OBJECTIVES"})
        issues.extend([{"code": "QUANTITY_UNIT_UNVERIFIED"}, {"code": "CURRENCY_UNCONFIGURED"}])
        result["data_quality"] = {"issues": issues, "coverage": context["coverage"],
            "expectation_model": {"status": "not_modeled", "supported_future_context": ["business_day", "intramonth", "annual", "channel", "structure"]},
            "evidence": [{"id": context["context_id"], "source": context["source"], "metric_version": "existing_v1",
                          "record_count": len(context["records"])}]}
        result["status"] = "unavailable" if not complete else "partial" if issues else "ok"
        return result

    def drilldown(self, query, user):
        request = sales_request(query)
        context = self.context_service.build(request, user)
        return {"schema_version": "1.0", "context_id": context["context_id"],
                "coverage": context["coverage"], **self._drilldown(request, context)}

    def _drilldown(self, request, context):
        dimension = request["dimension"]
        key_field, name_field = DIMENSIONS[dimension]
        grouped = defaultdict(lambda: {"current": [], "mom": [], "yoy": []})
        for window in ("current", "mom", "yoy"):
            for row in self._slice(context, window):
                grouped[str(row.get(key_field) or "__unclassified__")][window].append(row)
        complete = context["coverage"]["current"]["status"] == "complete"
        comparable = complete and context["coverage"]["mom"]["status"] == "complete"
        nodes = []
        for key, groups in grouped.items():
            sample = next(iter(groups["current"] or groups["mom"] or groups["yoy"]))
            sales = self._comparison(_sum_amount(groups["current"]), _sum_amount(groups["mom"]), comparable)
            nodes.append({"key": key, "name": str(sample.get(name_field) or key), "sales": sales,
                "sales_yoy": self._comparison(_sum_amount(groups["current"]), _sum_amount(groups["yoy"]),
                    complete and context["coverage"]["yoy"]["status"] == "complete"),
                "quantity": self._comparison(_sum_quantity(groups["current"]), _sum_quantity(groups["mom"]), comparable),
                "active_clients": len({r.get("client_key") for r in groups["current"] if r.get("client_key")}) if complete else None,
                "additive_metrics": ["sales", "quantity"], "non_additive_metrics": ["active_clients"]})
        nodes.sort(key=lambda n: (-(abs(n["sales"]["delta"] or 0)), n["key"]))
        total = len(nodes)
        all_current = _sum_amount(self._slice(context, "current"))
        all_previous = _sum_amount(self._slice(context, "mom"))
        return {"dimension": dimension, "filters": request["filters"], "hierarchy": HIERARCHY,
                "available_dimensions": list(DIMENSIONS), "structure_alias": "sales_force",
                "nodes": nodes[request["offset"]:request["offset"] + request["limit"]],
                "total_nodes": total, "limit": request["limit"], "offset": request["offset"],
                "totals": self._comparison(all_current, all_previous, comparable),
                "ranking": "absolute_sales_delta", "endpoint": "/api/intelligence/sales/drilldown"}

    def _objectives(self, request, context, user, current):
        # Never apply company-wide goals to a narrower permission universe.
        if context["has_restrictions"]:
            return []
        filters = {k: v for k, v in request["filters"].items() if k != "company"}
        company = request["filters"].get("company")
        if not company:
            companies = {r.get("company_key") for r in current if r.get("company_key")}
            company = next(iter(companies)) if len(companies) == 1 else None
        if not company:
            return []
        scope_alias = {"structure": "commercial_structure", "sales_force": "sales_force", "seller": "seller",
                       "channel": "channel", "family": "family", "line": "line", "route": "route"}
        if len(filters) > 1 or any(k not in scope_alias for k in filters):
            return []
        scope_type, scope_key = ("company", company) if not filters else (scope_alias[next(iter(filters))], next(iter(filters.values())))
        documents = list(self.db["commercial_objectives"].find({"company_key": company,
            "period": request["as_of"].strftime("%Y-%m"), "current": True, "status": "active",
            "scope_type": scope_type, "scope_key": scope_key}, {"_id": 0}))
        engine = ObjectiveService(self.db, initialize_indexes=False)
        actuals = {"net_sales": _sum_amount(current), "quantity": _sum_quantity(current),
                   "active_clients": len({r.get("client_key") for r in current if r.get("client_key")}),
                   "mix": len({r.get("product_key") for r in current if r.get("product_key")})}
        results = []
        for obj in documents:
            if obj["metric"] not in actuals:
                continue  # First-ever/recovered need independently certified lifetime coverage.
            for field in ("target_value", "baseline_value"):
                if hasattr(obj.get(field), "to_decimal"):
                    obj[field] = str(obj[field].to_decimal())
            progress = engine.calculate_progress(obj, as_of=request["as_of"],
                metric_provider=lambda metric, *_: Decimal(str(actuals[metric])))
            results.append({"objective_id": obj["objective_id"], "version": obj.get("version"),
                            "metric": obj["metric"], "target": float(obj["target_value"]), "progress": progress})
        return results
