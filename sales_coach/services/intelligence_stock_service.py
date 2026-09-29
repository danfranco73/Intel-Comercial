from collections import defaultdict
from datetime import datetime, timezone
import os
from sales_coach.domain.intelligence import envelope, cross_domain_readiness
from sales_coach.repositories.deposit_repository import DepositRepository
from sales_coach.repositories.stock_snapshot_repository import StockSnapshotRepository
from sales_coach.schemas.intelligence import page


class IntelligenceStockService:
    def __init__(self, db):
        self.db = db
        self.repository = StockSnapshotRepository(db)

    @staticmethod
    def authorize(user):
        # No reliable location entitlements exist for other roles yet.
        if user.role != "admin":
            raise PermissionError("Stock requiere administración hasta validar permisos por ubicación")

    def brief(self, query, user):
        self.authorize(user)
        if set(query) - {"snapshot_id"}:
            raise ValueError("Parámetro desconocido")
        snapshot = self.repository.select(query.get("snapshot_id"))
        result = envelope("stock", {})
        catalog = DepositRepository(self.db).configuration()
        result["data_quality"].update(execution_coverage="not_captured",
            universe_coverage=catalog["universe_coverage"], capabilities={
                "physical_stock": True, "available_stock": False, "committed_stock": False,
                "days_of_supply": False, "transfer_recommendations": False})
        if snapshot is None:
            result.update(status="unavailable", summary={"snapshot_id": None})
            result["data_quality"]["issues"].append({"code": "NO_STOCK_SNAPSHOT"})
            return result
        result["period"] = {"stock_date": snapshot["requested_stock_date"],
                            "capture_started_at": snapshot["started_at"], "capture_finished_at": snapshot["finished_at"]}
        configuration = snapshot["configuration"]
        mapping = {d["deposit_id"]: d for d in configuration["deposits"]}
        rows = list(self.repository.rows.find({"snapshot_id": snapshot["snapshot_id"]}, {"_id": 0, "raw": 0}))
        groups = defaultdict(list)
        for row in rows:
            depot = mapping.get(row["deposit_id"], {})
            verified = depot.get("relationships_verified") is True
            groups[(depot.get("company") if verified else None, depot.get("branch") if verified else None,
                    row["deposit_id"], row["warehouse_id"])].append(row)
        nodes = [{"company": k[0], "branch": k[1], "deposit_id": k[2], "warehouse_id": k[3],
                  "warehouse_metadata_status": "unverified", "rows": len(v),
                  "coverage_status": next((d["coverage_status"] for d in snapshot["deposits"] if d["deposit_id"] == k[2]), "unknown"),
                  "distinct_articles": len({r["physical_article_id"] for r in v})} for k, v in groups.items()]
        result["summary"] = {"snapshot_id": snapshot["snapshot_id"], "observations": len(rows),
            "distinct_articles": len({r["physical_article_id"] for r in rows}),
            "expected_deposits": snapshot["expected_deposit_ids"], "deposits": snapshot["deposits"],
            "quantity_total": None, "quantity_total_reason": "HETEROGENEOUS_PRODUCTS_AND_UNITS"}
        result["drilldowns"] = {"hierarchy": ["group", "company", "branch", "deposit", "warehouse", "product"],
            "nodes": nodes, "items_endpoint": "/api/intelligence/stock/items"}
        quality = result["data_quality"]
        identity_counts = {status: sum(r.get("product_identity", {}).get("status", "unknown") == status for r in rows)
                           for status in ("verified", "ambiguous", "unknown")}
        quality["product_identity"] = {"version": snapshot.get("identity_version"), "row_counts": identity_counts}
        quality["cross_domain"] = cross_domain_readiness({"status": "verified" if rows and
            identity_counts["verified"] == len(rows) else "unverified"})
        if identity_counts["unknown"] or identity_counts["ambiguous"]:
            quality["issues"].append({"code": "PRODUCT_IDENTITY_UNVERIFIED"})
        captured = datetime.fromisoformat(snapshot["started_at"])
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        age = max(0, (datetime.now(timezone.utc) - captured).total_seconds())
        quality["capture_age_seconds"] = round(age)
        max_age = max(1, int(os.getenv("INTELLIGENCE_STOCK_MAX_AGE_HOURS", "24")))
        quality["max_capture_age_hours"] = max_age
        if age > max_age * 3600:
            quality["issues"].append({"code": "STALE_STOCK_CAPTURE"})
        quality.update(execution_coverage=snapshot["execution_coverage"],
                       universe_coverage=configuration["universe_coverage"],
                       evidence=[{"id": snapshot["snapshot_id"], "source": "ChessERP/stock",
                                  "catalog_fingerprint": configuration["catalog_fingerprint"]}])
        if configuration["universe_coverage"]["status"] != "verified":
            quality["issues"].append({"code": "UNIVERSE_UNVERIFIED"})
        if catalog["catalog_fingerprint"] != configuration["catalog_fingerprint"]:
            quality["issues"].append({"code": "CATALOG_CHANGED_SINCE_CAPTURE"})
        if any(n["company"] is None or n["branch"] is None for n in nodes):
            quality["issues"].append({"code": "LOCATION_RELATIONSHIPS_UNVERIFIED"})
        latest = self.repository.headers.find_one({}, {"_id": 0}, sort=[("started_at", -1)])
        if latest and latest["snapshot_id"] != snapshot["snapshot_id"]:
            quality["latest_attempt"] = {"snapshot_id": latest["snapshot_id"], "status": latest["status"],
                                         "started_at": latest["started_at"]}
            quality["issues"].append({"code": "NEWER_CAPTURE_EXISTS"})
        result["status"] = "unavailable" if not rows else "ok" if snapshot["status"] == "complete" and not quality["issues"] else "partial"
        return result

    def items(self, query, user):
        self.authorize(user)
        allowed = {"snapshot_id", "deposit_id", "warehouse_id", "physical_article_id", "company", "branch", "limit", "offset"}
        if set(query) - allowed:
            raise ValueError("Parámetro desconocido")
        snapshot = self.repository.select(query.get("snapshot_id"))
        if not snapshot:
            raise LookupError("Snapshot no encontrado")
        limit, offset = page(query)
        match = {"snapshot_id": snapshot["snapshot_id"]}
        for field in ("deposit_id", "warehouse_id", "physical_article_id"):
            if field in query:
                match[field] = str(query[field])
        if "company" in query or "branch" in query:
            eligible = [d["deposit_id"] for d in snapshot["configuration"]["deposits"]
                        if d.get("relationships_verified") and all(d.get(f) == query[f] for f in ("company", "branch") if f in query)]
            if "deposit_id" in match:
                eligible = [d for d in eligible if d == match["deposit_id"]]
            match["deposit_id"] = {"$in": eligible}
        rows = list(self.repository.rows.find(match, {"_id": 0}).sort([("deposit_id", 1), ("ordinal", 1)]).skip(offset).limit(limit))
        return {"schema_version": "1.0", "snapshot_id": snapshot["snapshot_id"], "rows": rows,
                "total": self.repository.rows.count_documents(match), "limit": limit, "offset": offset}
