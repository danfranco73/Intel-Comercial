from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from analyzer import analyze_datasets
from mongo_client import (
    load_erp_articles_dataset,
    load_erp_routes_dataset,
    load_erp_sellers_dataset,
)
from sales_coach.repositories import SalesRepository
from sales_coach.repositories import CoachRuleRepository
from sales_coach.domain import CoachCommentEngine
from sales_coach.services.objective_service import ObjectiveService


class SalesCoachService:
    def __init__(
        self,
        db,
        sales_loader: Callable[[str, str], tuple[dict[str, Any], str]] | None = None,
    ):
        self.db = db
        self.sales_loader = sales_loader or SalesRepository().load_preferred

    def home(self, fecha_desde: str, fecha_hasta: str, user, data_scope) -> dict[str, Any]:
        report, source = self._report(fecha_desde, fecha_hasta, data_scope)
        sellers = report["dashboards"]["sellers"]
        clients = report["dashboards"]["clients"]
        opportunities = report["dashboards"]["opportunities"]
        rows = sellers.get("rows") or []
        net_sales = round(sum(item.get("sales", 0) or 0 for item in rows), 2)
        previous_net_sales = round(
            sum(item.get("previousSales", 0) or 0 for item in rows), 2
        )
        quantity = round(sum(item.get("quantity", 0) or 0 for item in rows), 2)
        orders = sum(item.get("orders", 0) or 0 for item in rows)
        objectives = ObjectiveService(self.db).list(
            user, data_scope, period=fecha_hasta[:7]
        )
        fulfillment_values = [
            item["progress"]["fulfillment_pct"]
            for item in objectives
            if item.get("status") == "active" and item.get("progress")
        ]
        return {
            "meta": {**report["meta"], "source": source},
            "summary": {
                **report["summary"],
                "netSales": net_sales,
                "quantity": quantity,
                "activeClients": clients["summary"]["activeCount"],
                "avgTicket": round(net_sales / max(orders, 1), 2),
                "valuePerQuantity": round(net_sales / max(quantity, 1), 2),
                "growthPct": self._pct_change(net_sales, previous_net_sales),
                "objectiveFulfillmentPct": round(
                    sum(fulfillment_values) / max(len(fulfillment_values), 1), 1
                ),
            },
            "sellerSummary": sellers["summary"],
            "clientSummary": clients["summary"],
            "opportunitySummary": opportunities["summary"],
            "sellersGrowing": [row for row in rows if row["growthPct"] > 0][:5],
            "sellersAtRisk": sorted(
                [row for row in rows if row["status"] == "En riesgo"],
                key=lambda item: item["growthPct"],
            )[:5],
            "alerts": (report.get("alerts") or [])[:5],
            "objectives": objectives,
            "updatedAt": self._freshness(),
        }

    def sellers(self, fecha_desde: str, fecha_hasta: str, data_scope) -> dict[str, Any]:
        report, source = self._report(fecha_desde, fecha_hasta, data_scope)
        dashboard = report["dashboards"]["sellers"]
        rule_set = CoachRuleRepository(self.db).active_rules("seller")
        self._apply_comments(dashboard.get("rows") or [], rule_set)
        return {
            "meta": {**report["meta"], "source": source},
            **dashboard,
            "ruleSet": {
                "id": rule_set.get("rule_set_id"),
                "version": rule_set.get("version"),
            },
        }

    def seller_detail(
        self,
        seller_key: str,
        fecha_desde: str,
        fecha_hasta: str,
        user,
        data_scope,
    ) -> dict[str, Any]:
        seller = self._authorize_seller(seller_key, user, data_scope)
        report, source, datasets = self._report(
            fecha_desde, fecha_hasta, data_scope, include_datasets=True
        )
        row = next(
            (
                item
                for item in report["dashboards"]["sellers"]["rows"]
                if item.get("sellerKey") == seller_key
                or item.get("seller") == seller.get("seller_name")
            ),
            None,
        )
        if row is None:
            raise LookupError("El vendedor no tiene ventas en el período seleccionado")
        team_rows = report["dashboards"]["sellers"]["rows"]
        team_sales = [item["sales"] for item in team_rows if item.get("sellerKey")]
        top_quartile = (
            sorted(team_sales, reverse=True)[max(int(len(team_sales) * 0.25) - 1, 0)]
            if team_sales
            else 0
        )
        objective_items = ObjectiveService(self.db).list(
            user, data_scope, period=fecha_hasta[:7]
        )
        seller_objectives = [
            item
            for item in objective_items
            if item["scope_type"] == "seller" and item["scope_key"] == seller_key
        ]
        active_objective = next(
            (item for item in seller_objectives if item.get("status") == "active"),
            None,
        )
        if active_objective:
            row["objectiveFulfillmentPct"] = active_objective["progress"]["fulfillment_pct"]
            row["objectiveTarget"] = float(active_objective["target_value"])
        client_rows = [
            item
            for item in report["dashboards"]["clients"]["rows"]
            if item.get("seller") == row["seller"]
        ]
        yoy = self._seller_yoy(
            datasets["sales"]["records"], seller_key, fecha_desde, fecha_hasta
        )
        rule_repository = CoachRuleRepository(self.db)
        rule_set = rule_repository.active_rules("seller")
        evaluation = self._apply_comments(team_rows, rule_set, target_row=row)
        audit_id = rule_repository.audit(
            user_id=user.id,
            entity_type="seller",
            entity_key=seller_key,
            date_range={"fechaDesde": fecha_desde, "fechaHasta": fecha_hasta},
            rule_set=rule_set,
            result=evaluation,
        )
        return {
            "meta": {**report["meta"], "source": source},
            "identification": {
                "sellerKey": seller_key,
                "name": seller.get("seller_name") or row["seller"],
                "salesForce": seller.get("sales_force") or row["salesForce"],
                "supervisorKey": seller.get("supervisor_key"),
                "branchKey": seller.get("branch_key"),
                "routes": sorted(
                    {
                        item.get("route")
                        for item in client_rows
                        if item.get("route") and item.get("route") != "Sin ruta"
                    }
                ),
                "period": f"{fecha_desde} a {fecha_hasta}",
            },
            "kpis": row,
            "comparisons": {
                "previousPeriodSales": row["previousSales"],
                "teamAverageSales": round(sum(team_sales) / max(len(team_sales), 1), 2),
                "teamTop25Threshold": round(top_quartile, 2),
                "yearOverYearSales": yoy,
            },
            "strengths": evaluation["strengths"],
            "opportunities": evaluation["opportunities"],
            "actionPlan": evaluation["actionPlan"],
            "objectives": seller_objectives,
            "clients": client_rows[:30],
            "rankingDefinitions": report["dashboards"]["sellers"]["rankingDefinitions"],
            "updatedAt": self._freshness(),
            "commentAudit": {
                "auditId": audit_id,
                "ruleSetId": rule_set.get("rule_set_id"),
                "ruleSetVersion": rule_set.get("version"),
                "deterministic": True,
            },
        }

    def client_detail(
        self,
        client_key: str,
        fecha_desde: str,
        fecha_hasta: str,
        user,
        data_scope,
    ) -> dict[str, Any]:
        report, source, datasets = self._report(
            fecha_desde, fecha_hasta, data_scope, include_datasets=True
        )
        matching = [
            item
            for item in datasets["sales"]["records"]
            if str(item.get("client_key") or "") == client_key
        ]
        if not matching:
            raise LookupError("Cliente no encontrado dentro de tu alcance")
        self._authorize_client(matching, user, data_scope)
        current_start = date.fromisoformat(fecha_desde)
        current_end = date.fromisoformat(fecha_hasta)
        current = [
            item
            for item in matching
            if current_start <= self._as_date(item.get("date")) <= current_end
        ]
        previous_days = (current_end - current_start).days + 1
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=previous_days - 1)
        previous = [
            item
            for item in matching
            if previous_start <= self._as_date(item.get("date")) <= previous_end
        ]
        sales = self._sum(current, "amount_net")
        previous_sales = self._sum(previous, "amount_net")
        products = sorted(
            {
                item.get("product_name") or item.get("product_key")
                for item in current
                if item.get("product_name") or item.get("product_key")
            }
        )
        families = sorted(
            {item.get("family") for item in current if item.get("family") and item.get("family") != "Sin familia"}
        )
        available_families = {
            item.get("family")
            for item in datasets.get("articles", {}).get("records", [])
            if item.get("family")
        }
        last_purchase = max(
            (self._as_date(item.get("date")) for item in matching), default=None
        )
        ticket_orders = {
            item.get("invoice") for item in current if item.get("invoice")
        }
        return {
            "meta": {**report["meta"], "source": source},
            "identification": {
                "clientKey": client_key,
                "name": next(
                    (item.get("client_name") for item in reversed(matching) if item.get("client_name")),
                    client_key,
                ),
                "sellerKey": next((item.get("seller_key") for item in current if item.get("seller_key")), None),
                "seller": next((item.get("seller_name") for item in current if item.get("seller_name")), None),
                "route": next((item.get("route_description") for item in current if item.get("route_description")), None),
            },
            "kpis": {
                "sales": sales,
                "previousSales": previous_sales,
                "growthPct": self._pct_change(sales, previous_sales),
                "quantity": self._sum(current, "quantity"),
                "frequency": len({self._as_date(item.get("date")) for item in current}),
                "orders": len(ticket_orders),
                "avgTicket": round(sales / max(len(ticket_orders), 1), 2),
                "mixProducts": len(products),
                "mixFamilies": len(families),
                "lastPurchase": last_purchase.isoformat() if last_purchase else None,
                "recencyDays": (current_end - last_purchase).days if last_purchase else None,
            },
            "products": products[:50],
            "families": families,
            "absentCategories": sorted(available_families - set(families))[:30],
            "history": self._monthly_history(matching),
            "risk": self._client_risk(current, previous, last_purchase, current_end),
            "opportunities": self._client_opportunities(products, families, sales, previous_sales),
            "updatedAt": self._freshness(),
        }

    def _report(self, fecha_desde, fecha_hasta, data_scope, include_datasets=False):
        start = date.fromisoformat(fecha_desde)
        end = date.fromisoformat(fecha_hasta)
        if start > end:
            raise ValueError("fechaDesde no puede ser posterior a fechaHasta")
        days = (end - start).days + 1
        comparison_end = start - timedelta(days=1)
        comparison_start = comparison_end - timedelta(days=days - 1)
        try:
            yoy_start = start.replace(year=start.year - 1)
        except ValueError:
            yoy_start = start.replace(year=start.year - 1, day=28)
        load_start = min(comparison_start, yoy_start)
        sales, source = self.sales_loader(load_start.isoformat(), end.isoformat())
        sales = {
            **sales,
            "analysisRange": {"fechaDesde": start.isoformat(), "fechaHasta": end.isoformat()},
            "comparisonRange": {
                "fechaDesde": comparison_start.isoformat(),
                "fechaHasta": comparison_end.isoformat(),
            },
            "loadRange": {"fechaDesde": load_start.isoformat(), "fechaHasta": end.isoformat()},
            "comparisonDays": days,
        }
        datasets = {"sales": sales}
        for key, loader in (
            ("articles", load_erp_articles_dataset),
            ("sellers", load_erp_sellers_dataset),
            ("routes", load_erp_routes_dataset),
        ):
            try:
                datasets[key] = loader()
            except Exception:
                pass
        report = analyze_datasets(datasets, scope_filters=data_scope)
        if include_datasets:
            return report, source, datasets
        return report, source

    def _authorize_seller(self, seller_key, user, data_scope):
        seller = self.db["erp_sellers"].find_one({"seller_key": seller_key}, {"_id": 0})
        if seller is None:
            raise LookupError("Vendedor no encontrado")
        if user.role == "seller" and seller_key != user.seller_key:
            raise PermissionError("No podés consultar otro vendedor")
        allowed_names = set((data_scope or {}).get("seller_name") or [])
        if allowed_names and seller.get("seller_name") not in allowed_names:
            raise PermissionError("El vendedor está fuera de tu alcance")
        return seller

    @staticmethod
    def _authorize_client(records, user, data_scope):
        if user.role == "seller" and not any(
            item.get("seller_key") == user.seller_key for item in records
        ):
            raise PermissionError("El cliente está fuera de tu cartera")
        allowed_names = set((data_scope or {}).get("seller_name") or [])
        if allowed_names and not any(item.get("seller_name") in allowed_names for item in records):
            raise PermissionError("El cliente está fuera de tu alcance")

    def _seller_yoy(self, records, seller_key, start_text, end_text):
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
        try:
            yoy_start = start.replace(year=start.year - 1)
            yoy_end = end.replace(year=end.year - 1)
        except ValueError:
            yoy_start = start.replace(year=start.year - 1, day=28)
            yoy_end = end.replace(year=end.year - 1, day=28)
        matching = [
            item
            for item in records
            if item.get("seller_key") == seller_key
            and yoy_start <= self._as_date(item.get("date")) <= yoy_end
        ]
        return self._sum(matching, "amount_net")

    def _freshness(self):
        row = self.db["sync_runs"].find_one(
            {"entity": "sales", "status": {"$in": ["success", "warning"]}},
            {"finished_at": 1},
            sort=[("finished_at", -1)],
        )
        value = row.get("finished_at") if row else None
        return value.isoformat() if hasattr(value, "isoformat") else None

    @staticmethod
    def _apply_comments(rows, rule_set, target_row=None):
        visible = [item for item in rows if item.get("sellerKey")]
        context = {
            "teamGrowthPct": sum(item.get("growthPct", 0) for item in visible) / max(len(visible), 1),
            "teamMixAverage": sum(item.get("mixCount", 0) for item in visible) / max(len(visible), 1),
            "teamClientsAverage": sum(item.get("clients", 0) for item in visible) / max(len(visible), 1),
            "teamTicketAverage": sum(item.get("avgTicket", 0) for item in visible) / max(len(visible), 1),
            "teamQuantityAverage": sum(item.get("quantity", 0) for item in visible) / max(len(visible), 1),
        }
        engine = CoachCommentEngine(rule_set.get("rules") or [])
        if target_row is not None:
            return engine.evaluate(target_row, context)
        last_result = None
        for row in rows:
            result = engine.evaluate(row, context)
            row["strengths"] = result["strengths"]
            row["opportunities"] = result["opportunities"]
            row["actionPlan"] = result["actionPlan"]
            row["commentRuleSetVersion"] = rule_set.get("version")
            last_result = result
        return last_result or {
            "strengths": [],
            "opportunities": [],
            "actionPlan": [],
        }

    @staticmethod
    def _monthly_history(records):
        grouped: dict[str, float] = {}
        for item in records:
            period = SalesCoachService._as_date(item.get("date")).strftime("%Y-%m")
            value = item.get("amount_net") if item.get("amount_net") is not None else item.get("amount")
            grouped[period] = grouped.get(period, 0) + float(value or 0)
        return [
            {"period": period, "sales": round(value, 2)}
            for period, value in sorted(grouped.items())
        ]

    @staticmethod
    def _client_risk(current, previous, last_purchase, end):
        current_sales = SalesCoachService._sum(current, "amount_net")
        previous_sales = SalesCoachService._sum(previous, "amount_net")
        recency = (end - last_purchase).days if last_purchase else None
        level = "high" if not current or (recency is not None and recency > 60) else "medium" if current_sales < previous_sales * 0.7 else "low"
        return {
            "level": level,
            "evidence": {
                "currentSales": current_sales,
                "previousSales": previous_sales,
                "recencyDays": recency,
            },
        }

    @staticmethod
    def _client_opportunities(products, families, sales, previous_sales):
        items = []
        if len(families) <= 1:
            items.append(
                {
                    "type": "cross_sell",
                    "message": f"Compra sólo {len(families)} familia; revisar categorías complementarias.",
                    "evidence": {"families": len(families)},
                }
            )
        if sales < previous_sales:
            items.append(
                {
                    "type": "recovery",
                    "message": f"La venta cayó {round(previous_sales - sales, 2)} contra el período anterior.",
                    "evidence": {"currentSales": sales, "previousSales": previous_sales},
                }
            )
        if not items:
            items.append(
                {
                    "type": "retention",
                    "message": f"Sostener frecuencia y ampliar el mix actual de {len(products)} productos.",
                    "evidence": {"products": len(products)},
                }
            )
        return items[:3]

    @staticmethod
    def _sum(records, field):
        return round(
            sum(
                float(
                    item.get(field)
                    if item.get(field) is not None
                    else ((item.get("amount") or 0) if field == "amount_net" else 0)
                )
                for item in records
            ),
            2,
        )

    @staticmethod
    def _pct_change(current, previous):
        if not previous:
            return 100.0 if current else 0.0
        return round((current - previous) / abs(previous) * 100, 1)

    @staticmethod
    def _as_date(value):
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])
