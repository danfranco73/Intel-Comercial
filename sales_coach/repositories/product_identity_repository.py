from collections import defaultdict
from sales_coach.domain.intelligence import now_iso, fingerprint


class ProductIdentityRepository:
    def __init__(self, db):
        self.collection = db["intelligence_product_identities"]
        self.settings = db["intelligence_settings"]

    def sync(self, records):
        grouped = defaultdict(set)
        for row in records:
            if row.get("physical_article_id"):
                targets = grouped[str(row["physical_article_id"])]
                if row.get("statistical_article_id"):
                    targets.add(str(row["statistical_article_id"]))
        version = fingerprint({k: sorted(v) for k, v in grouped.items()})
        for physical, targets in grouped.items():
            self.collection.update_one({"_id": version + ":" + physical}, {"$setOnInsert": {
                "physical_article_id": physical, "statistical_article_ids": sorted(targets),
                "status": "verified" if len(targets) == 1 else "ambiguous" if targets else "unknown", "updated_at": now_iso(),
                "source": "ChessERP/articles", "version": version}}, upsert=True)
        self.settings.update_one({"_id": "product_identity_version"}, {"$set": {"version": version}}, upsert=True)
        return {"identities": len(grouped), "ambiguous": sum(len(v) > 1 for v in grouped.values()),
                "unknown": sum(not v for v in grouped.values()), "version": version}

    def version(self):
        return (self.settings.find_one({"_id": "product_identity_version"}) or {}).get("version")

    def bindings(self, version):
        if not version:
            return {}
        rows = list(self.collection.find({"version": version}, {"_id": 0}))
        inverse = defaultdict(set)
        for row in rows:
            for target in row["statistical_article_ids"]:
                inverse[target].add(row["physical_article_id"])
        result = {}
        for row in rows:
            targets = row["statistical_article_ids"]
            unique = len(targets) == 1 and len(inverse[targets[0]]) == 1
            result[row["physical_article_id"]] = {"version": version,
                "statistical_article_ids": targets, "status": "verified" if unique else "ambiguous" if targets else "unknown"}
        return result

    def resolve(self, physical_id, version=None):
        selected_version = version or self.version()
        row = self.collection.find_one({"physical_article_id": str(physical_id), "version": selected_version}, {"_id": 0})
        if row and row["status"] == "verified" and self.collection.count_documents({
                "version": selected_version, "statistical_article_ids": row["statistical_article_ids"][0]}) != 1:
            row["status"] = "ambiguous"
        return row or {"physical_article_id": str(physical_id), "status": "unknown"}

    def physical_for_statistical(self, statistical_id, version=None):
        rows = list(self.collection.find({"statistical_article_ids": str(statistical_id), "version": version or self.version()}, {"_id": 0}))
        if len(rows) != 1 or rows[0]["status"] != "verified":
            return {"status": "ambiguous" if rows else "unknown", "physical_article_id": None}
        return rows[0]
