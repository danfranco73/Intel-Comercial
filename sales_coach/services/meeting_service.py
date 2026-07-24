from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from security.authorization import merge_data_scope
from sales_coach.repositories.meeting_repository import MeetingRepository
from sales_coach.schemas.meetings import MeetingCreateRequest, MeetingPdfRequest
from sales_coach.services.alert_service import AlertService
from sales_coach.services.objective_service import ObjectiveService
from sales_coach.services.sales_coach_service import SalesCoachService
from sales_coach.services.pdf_service import MeetingPdfRenderer
from sales_coach.services.pptx_service import MeetingPptxRenderer


class MeetingService:
    def __init__(self, db, sales_coach: SalesCoachService | None = None):
        self.db = db
        self.repository = MeetingRepository(db)
        self.sales_coach = sales_coach or SalesCoachService(db)

    def create(self, payload: Any, user, data_scope) -> dict[str, Any]:
        if user.role == "viewer":
            raise PermissionError("El perfil de consulta no puede generar informes")
        request = MeetingCreateRequest.parse(payload)
        requested_scope = self._requested_scope(request, user)
        scope = merge_data_scope(requested_scope, data_scope)
        report, source = self.sales_coach._report(
            request.fecha_desde, request.fecha_hasta, scope
        )
        seller_rows = [
            row for row in report["dashboards"]["sellers"].get("rows", [])
            if row.get("sellerKey") and row.get("seller") != "Sin vendedor"
        ]
        if request.seller_keys:
            allowed = set(request.seller_keys)
            seller_rows = [row for row in seller_rows if row.get("sellerKey") in allowed]
        if user.role == "seller":
            seller_rows = [row for row in seller_rows if row.get("sellerKey") == user.seller_key]
        if request.report_type == "seller_packet" and not seller_rows:
            raise ValueError("No hay vendedores autorizados con datos para generar fichas")
        period = {"start": request.fecha_desde, "end": request.fecha_hasta}
        objectives = ObjectiveService(self.db).list(
            user, data_scope, period=request.fecha_hasta[:7]
        )
        alerts = AlertService(self.db).list(
            user, data_scope, period=request.fecha_hasta[:7]
        )
        structured = self._build_payload(
            report, seller_rows, objectives, alerts, period, source, request.filters
        )
        created = self.repository.create(
            {
                "company_key": "CODENOA",
                "report_type": request.report_type,
                "period": period,
                "comparison_period": report.get("meta", {}).get("comparison"),
                "filters": request.filters,
                "seller_keys": [row["sellerKey"] for row in seller_rows],
                "scope_snapshot": scope,
                "data_updated_at": self.sales_coach._freshness(),
                "payload": structured,
            },
            user.id,
        )
        return created

    def get(self, report_id: str, user, data_scope) -> dict[str, Any]:
        report = self.repository.get(report_id)
        if not report:
            raise LookupError("Informe no encontrado")
        self._authorize_snapshot(report, user, data_scope)
        return report

    def list(self, user, data_scope) -> list[dict[str, Any]]:
        reports = self.repository.list({})
        visible = []
        for report in reports:
            try:
                self._authorize_snapshot(report, user, data_scope)
                visible.append(report)
            except PermissionError:
                continue
        return visible

    def pdf(self, payload: Any, user, data_scope) -> tuple[bytes, str]:
        request = MeetingPdfRequest.parse(payload)
        report = self.get(request.report_id, user, data_scope)
        content = MeetingPdfRenderer().render(report)
        filename = (
            f"codenoa_{report['report_type']}_{report['period']['end']}_"
            f"v{report['version']}.pdf"
        )
        return content, filename

    def pptx(self, payload: Any, user, data_scope) -> tuple[bytes, str]:
        request = MeetingPdfRequest.parse(payload)
        report = self.get(request.report_id, user, data_scope)
        content = MeetingPptxRenderer().render(report)
        filename = (
            f"codenoa_{report['report_type']}_{report['period']['end']}_"
            f"v{report['version']}.pptx"
        )
        return content, filename

    def _requested_scope(self, request, user):
        filters = dict(request.filters)
        branch = filters.pop("branch_key", [])
        supervisor = filters.pop("supervisor_key", [])
        seller_keys = list(request.seller_keys)
        seller_query: dict[str, Any] = {}
        clauses = []
        if branch:
            clauses.append({"branch_key": {"$in": branch}})
        if supervisor:
            clauses.append({"supervisor_key": {"$in": supervisor}})
        if seller_keys:
            clauses.append({"seller_key": {"$in": seller_keys}})
        if clauses:
            seller_query = clauses[0] if len(clauses) == 1 else {"$and": clauses}
            names = [
                row["seller_name"]
                for row in self.db["erp_sellers"].find(seller_query, {"seller_name": 1})
                if row.get("seller_name")
            ]
            filters["seller_name"] = names
        if user.role == "seller":
            own = self.db["erp_sellers"].find_one(
                {"seller_key": user.seller_key}, {"seller_name": 1}
            )
            filters["seller_name"] = [own["seller_name"]] if own else []
        return filters

    def _build_payload(self, report, sellers, objectives, alerts, period, source, filters):
        clients = report["dashboards"]["clients"]
        opportunities = report["dashboards"]["opportunities"]
        total_sales = sum(float(row.get("sales") or 0) for row in sellers)
        previous_sales = sum(float(row.get("previousSales") or 0) for row in sellers)
        quantity = sum(float(row.get("quantity") or 0) for row in sellers)
        active_objectives = [item for item in objectives if item.get("status") == "active"]
        fulfillment = [
            float(item["progress"]["fulfillment_pct"])
            for item in active_objectives if item.get("progress")
        ]
        kpis = {
            "net_sales": round(total_sales, 2),
            "quantity": round(quantity, 2),
            "active_clients": clients.get("summary", {}).get("activeCount", 0),
            "growth_pct": self.sales_coach._pct_change(total_sales, previous_sales),
            "objective_fulfillment_pct": round(sum(fulfillment) / max(len(fulfillment), 1), 1),
            "opportunity_potential": opportunities.get("summary", {}).get("totalPotential", 0),
        }
        open_alerts = [
            item for item in alerts if item.get("status") not in {"resolved", "dismissed"}
        ]
        seller_pages = []
        for row in sellers:
            seller_pages.append(
                {
                    "identification": {
                        "seller_key": row.get("sellerKey"),
                        "name": row.get("seller"),
                        "sales_force": row.get("salesForce"),
                        "period": f"{period['start']} a {period['end']}",
                    },
                    "kpis": {
                        key: row.get(key)
                        for key in (
                            "sales", "quantity", "clients", "avgTicket", "growthPct",
                            "sharePct", "mixCount", "top3ClientsSharePct", "rankSales",
                            "recoveredClients", "droppedClients",
                        )
                    },
                    "strengths": row.get("strengths", [])[:3],
                    "opportunities": row.get("opportunities", [])[:3],
                    "action_plan": row.get("actionPlan", [])[:3],
                    "objectives": [
                        item for item in objectives
                        if item.get("scope_type") == "seller"
                        and item.get("scope_key") == row.get("sellerKey")
                    ],
                    "alerts": [
                        item for item in open_alerts
                        if item.get("seller_key") == row.get("sellerKey")
                    ][:5],
                }
            )
        return {
            "title": "Codenoa Sales Coach — Reunión comercial",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": period,
            "source": source,
            "filters": filters,
            "kpis": kpis,
            "executive_summary": [
                f"La venta neta del período fue {total_sales:.2f}, con una variación de {kpis['growth_pct']:.1f}%.",
                f"Se registraron {kpis['active_clients']} clientes activos y {quantity:.2f} unidades.",
                f"El potencial comercial priorizado alcanza {float(kpis['opportunity_potential'] or 0):.2f}.",
            ],
            "recognitions": [
                {
                    "seller": row.get("seller"),
                    "message": f"crecimiento {float(row.get('growthPct') or 0):.1f}% y venta {float(row.get('sales') or 0):.2f}",
                }
                for row in sorted(sellers, key=lambda item: item.get("growthPct", 0), reverse=True)[:3]
                if float(row.get("growthPct") or 0) > 0
            ],
            "ranking": sellers[:12],
            "objectives": active_objectives,
            "alerts": open_alerts[:12],
            "opportunities": report.get("dashboards", {}).get("opportunities", {}).get("rows", [])[:10],
            "monthly": report.get("dashboards", {}).get("history", {}).get("charts", {}).get("monthly", []),
            "seller_pages": seller_pages,
            "commitments_template": [
                "Acción prioritaria",
                "Responsable",
                "Fecha compromiso",
                "Resultado esperado",
            ],
        }

    def _authorize_snapshot(self, report, user, data_scope):
        keys = set(report.get("seller_keys") or [])
        if user.role in {"admin", "commercial_director", "viewer"}:
            return
        if user.role == "seller":
            if keys - {user.seller_key}:
                raise PermissionError("El informe contiene vendedores fuera de tu alcance")
            return
        allowed = set(self._allowed_seller_keys(data_scope))
        if keys - allowed:
            raise PermissionError("El informe contiene vendedores fuera de tu alcance")

    def _allowed_seller_keys(self, data_scope):
        names = list((data_scope or {}).get("seller_name") or [])
        return [
            row["seller_key"]
            for row in self.db["erp_sellers"].find(
                {"seller_name": {"$in": names}}, {"seller_key": 1}
            )
            if row.get("seller_key")
        ]
