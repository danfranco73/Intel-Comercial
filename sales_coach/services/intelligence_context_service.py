from bi.facts import enrich_sales_records
from bi.tactical_month import _month_range
from sales_coach.domain.intelligence import fingerprint
from sales_coach.repositories.sales_repository import SalesRepository
from sales_coach.schemas.intelligence import DIMENSIONS
from security.authorization import resolve_data_scope
from sales_coach.services.sales_coverage_service import SalesCoverageService


class IntelligenceContextService:
    def __init__(self, db, loader=None, coverage_service=None):
        self.db = db
        self.loader = loader or SalesRepository().load_intelligence
        self.coverage_service = coverage_service or SalesCoverageService(db)

    def build(self, request, user):
        end = request["as_of"]
        windows = _month_range(end)
        start = min(windows["yoyStart"], windows["previousStart"])
        rows, source, errors = self.loader(self.db, start.isoformat(), end.isoformat())
        loaded = {key: {"records": list(self.db[col].find({}, {"_id": 0}))} for key, col in (
            ("articles", "erp_articles"), ("sellers", "erp_sellers"), ("routes", "erp_routes"))}
        facts = enrich_sales_records(rows, loaded)
        scope = resolve_data_scope(user, self.db)
        seller_clauses = []
        if user.role == "supervisor":
            role_clauses = []
            if user.supervisor_key:
                role_clauses.append({"supervisor_key": user.supervisor_key})
            if user.sales_force_keys:
                role_clauses.append({"sales_force_key": {"$in": list(user.sales_force_keys)}})
            if user.branch_keys:
                role_clauses.append({"branch_key": {"$in": list(user.branch_keys)}})
            seller_clauses.append({"$or": role_clauses} if role_clauses else {"seller_key": {"$in": []}})
        elif user.role != "admin" and user.branch_keys:
            seller_clauses.append({"branch_key": {"$in": list(user.branch_keys)}})
        if user.role != "admin" and user.company_key:
            company = self.db["access_companies"].find_one({"company_key": user.company_key, "is_active": True}) or {}
            seller_clauses.append({"branch_key": {"$in": company.get("branch_keys") or []}})
        # Names are presentation labels and can collide across sellers.
        eligible_sellers = None if not seller_clauses else {str(r.get("seller_key")) for r in
            self.db["erp_sellers"].find({"$and": seller_clauses}, {"seller_key": 1}) if r.get("seller_key")}
        # Unknown constraints must never be dropped by UI filter normalization.
        def permitted(row):
            if eligible_sellers is not None and str(row.get("seller_key")) not in eligible_sellers:
                return False
            if any(str(row.get(k) or "") not in {str(v) for v in allowed} for k, allowed in scope.items()):
                return False
            if user.role == "seller" and str(row.get("seller_key") or "") != str(user.seller_key or "__none__"):
                return False
            if user.role != "admin" and user.seller_keys and row.get("seller_key") not in user.seller_keys:
                return False
            if user.role != "admin" and user.sales_force_keys and row.get("sales_scheme_key") not in user.sales_force_keys:
                return False
            return True
        facts = [r for r in facts if permitted(r)]
        if user.role != "admin" and user.company_key:
            company = self.db["access_companies"].find_one({"company_key": user.company_key, "is_active": True}) or {}
            keys = company.get("erp_company_keys")
            if not keys:
                raise PermissionError("La relación entre empresa de acceso y empresa ERP debe validarse")
            facts = [r for r in facts if str(r.get("company_key") or "") in keys]
        for dimension, value in request["filters"].items():
            field = DIMENSIONS[dimension][0]
            facts = [r for r in facts if str(r.get(field) or "__unclassified__") == value]
        coverage = self.coverage_service.assess(source, {
            "current": (windows["currentStart"], end),
            "mom": (windows["previousStart"], windows["previousEnd"]),
            "yoy": (windows["yoyStart"], windows["yoyEnd"]),
            "history": (start, end)})
        if user.role != "admin":
            coverage = {name: {k: v for k, v in data.items() if k not in {"observed_rows", "observed_days"}}
                        for name, data in coverage.items()}
        context_id = fingerprint({"facts": facts, "scope": scope, "user": user.id, "filters": request["filters"],
                                  "cutoff": end, "coverage": coverage})
        if request.get("context_id") and request["context_id"] != context_id:
            raise ValueError("El contexto cambió; volver a consultar el brief")
        return {"records": facts, "source": source, "issues": errors, "coverage": coverage,
                "windows": windows, "context_id": context_id, "loaded": loaded, "scope": scope,
                "has_restrictions": bool(scope) or eligible_sellers is not None or bool(user.company_key and user.role != "admin")}
