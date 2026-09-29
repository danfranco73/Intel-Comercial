"""Discovery is evidence; configuration and universe certification are explicit."""
from copy import deepcopy
from uuid import uuid4
from pymongo import ReturnDocument
from sales_coach.domain.intelligence import fingerprint, now_iso
from sales_coach.domain.stock import identifier


class DepositRepository:
    def __init__(self, db):
        self.db = db
        self.collection = db["intelligence_deposits"]
        self.settings = db["intelligence_settings"]

    def discover(self, records, source="sales_catalog"):
        for item in records:
            key = identifier(item.get("deposit_id") or item.get("deposit_key"))
            name = str(item.get("deposit_name") or key)
            self.collection.update_one({"_id": key}, {
                "$set": {"discovered": True, "discovered_name": name,
                          "discovery_source": source, "last_discovered_at": now_iso()},
                "$setOnInsert": {"deposit_id": key, "deposit_name": name, "configured": False,
                                 "company": None, "branch": None, "location": None,
                                 "active": None, "include_in_stock_analysis": False,
                                 "deposit_type": None, "notes": "", "relationships_verified": False,
                                 "revision": uuid4().hex}}, upsert=True)

    def list(self):
        return list(self.collection.find({}, {"_id": 0}).sort("deposit_id", 1))

    def configuration(self):
        rows = self.list()
        digest = fingerprint([{k: v for k, v in row.items() if k not in {
            "last_discovered_at", "discovered_name", "discovery_source"}} for row in rows])
        state = self.settings.find_one({"_id": "stock_universe"}, {"_id": 0}) or {}
        verified = state.get("catalog_fingerprint") == digest and state.get("verified") is True
        return {"deposits": rows, "catalog_fingerprint": digest,
                "universe_coverage": {"status": "verified" if verified else "unknown",
                                      "verified_at": state.get("verified_at") if verified else None}}

    def configure(self, payload, actor):
        if not isinstance(payload, dict):
            raise ValueError("Objeto requerido")
        key = identifier(payload.get("deposit_id"))
        fields = {"deposit_name", "company", "branch", "location", "active", "include_in_stock_analysis",
                  "deposit_type", "notes", "relationships_verified", "revision", "deposit_id"}
        if set(payload) - fields:
            raise ValueError("Campos desconocidos")
        current = self.collection.find_one({"_id": key})
        is_new = current is None
        if current is None:
            current = {"deposit_id": key, "deposit_name": key, "active": None,
                       "include_in_stock_analysis": False, "relationships_verified": False,
                       "revision": None, "discovered": False}
        if payload.get("revision") != current["revision"]:
            raise ValueError("Revisión desactualizada: consultar catálogo antes de guardar")
        new = {k: deepcopy(payload.get(k, current.get(k))) for k in fields - {"revision", "deposit_id"}}
        if any(new.get(k) != current.get(k) for k in ("company", "branch")) and "relationships_verified" not in payload:
            new["relationships_verified"] = False
        for field in ("active", "include_in_stock_analysis", "relationships_verified"):
            if not isinstance(new[field], bool):
                raise ValueError(f"{field} debe ser booleano explícito")
        if new["include_in_stock_analysis"] and not new["active"]:
            raise ValueError("Un depósito inactivo no puede incluirse en análisis")
        for field in ("deposit_name", "company", "branch", "location", "deposit_type", "notes"):
            if new[field] is not None and (not isinstance(new[field], str) or len(new[field]) > 2000):
                raise ValueError(f"{field} debe ser texto")
        if not new["deposit_name"]:
            raise ValueError("Nombre requerido")
        if new["relationships_verified"] and not (new["company"] or new["branch"]):
            raise ValueError("No hay relaciones para validar")
        new.update(configured=True, revision=uuid4().hex, updated_by=actor, updated_at=now_iso())
        new["deposit_id"] = key
        updated = self.collection.find_one_and_update({"_id": key, "revision": current["revision"]},
            {"$set": new, "$setOnInsert": {"discovered": False}}, upsert=is_new,
            return_document=ReturnDocument.AFTER)
        if updated is None:
            raise ValueError("Cambio concurrente: consultar catálogo")
        self.db["intelligence_deposit_audits"].insert_one({"deposit_id": key, "actor": actor,
            "at": now_iso(), "before": {k: v for k, v in current.items() if k != "_id"}, "after": new})
        updated.pop("_id", None)
        return updated

    def certify(self, payload, actor):
        if not isinstance(payload, dict) or set(payload) - {"catalog_fingerprint", "verified"}:
            raise ValueError("Se requieren catalog_fingerprint y verified")
        config = self.configuration()
        if payload.get("catalog_fingerprint") != config["catalog_fingerprint"]:
            raise ValueError("Catálogo cambió: revisar antes de certificar")
        if not isinstance(payload.get("verified"), bool):
            raise ValueError("verified debe ser booleano")
        if payload["verified"] and (not config["deposits"] or any(not d.get("configured") for d in config["deposits"])):
            raise ValueError("Clasificar todos los depósitos descubiertos antes de certificar")
        state = {"catalog_fingerprint": config["catalog_fingerprint"], "verified": payload["verified"],
                 "verified_by": actor, "verified_at": now_iso()}
        self.settings.update_one({"_id": "stock_universe"}, {"$set": state}, upsert=True)
        self.db["intelligence_deposit_audits"].insert_one({"action": "certify_universe", **state})
        return self.configuration()
