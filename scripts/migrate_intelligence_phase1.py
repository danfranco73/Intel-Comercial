"""Additive, idempotent provisioning. Seeds discovery, never activation."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mongo_client import get_db
from sales_coach.repositories.deposit_repository import DepositRepository


def migrate(db):
    db["intelligence_deposits"].create_index("deposit_id", unique=True)
    db["intelligence_stock_snapshots"].create_index("snapshot_id", unique=True)
    db["intelligence_stock_snapshots"].create_index([("status", 1), ("finished_at", -1)])
    db["intelligence_stock_rows"].create_index([("snapshot_id", 1), ("deposit_id", 1), ("ordinal", 1)], unique=True)
    db["intelligence_stock_rows"].create_index([("snapshot_id", 1), ("physical_article_id", 1)])
    db["intelligence_product_identities"].create_index("statistical_article_ids")
    db["intelligence_product_identities"].create_index([("version", 1), ("physical_article_id", 1)], unique=True)
    db["intelligence_stock_rejections"].create_index([("snapshot_id", 1), ("deposit_id", 1)])
    db["intelligence_deposit_audits"].create_index([("deposit_id", 1), ("at", -1)])
    repository = DepositRepository(db)
    seeds = json.loads((ROOT / "scripts/data/discovered_deposits_phase1.json").read_text())
    repository.discover(seeds, "approved_phase1_discovery")
    repository.discover(list(db["erp_deposits"].find({}, {"_id": 0})), "sales_catalog")
    config = repository.configuration()
    return {"discovered": sum(bool(d.get("discovered")) for d in config["deposits"]),
            "configured": sum(bool(d.get("configured")) for d in config["deposits"]),
            "included": sum(d.get("active") is True and d.get("include_in_stock_analysis") is True for d in config["deposits"]),
            "universe_coverage": config["universe_coverage"]}


if __name__ == "__main__":
    print(json.dumps(migrate(get_db()), ensure_ascii=False))
