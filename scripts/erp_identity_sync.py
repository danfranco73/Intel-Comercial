"""Read Chess article identities and publish a version; never writes to Chess."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from erp_client import fetch_articles_dataset
from mongo_client import get_db
from sales_coach.repositories.product_identity_repository import ProductIdentityRepository


def main():
    try:
        data = fetch_articles_dataset()
        result = ProductIdentityRepository(get_db()).sync(data["identity_records"])
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
