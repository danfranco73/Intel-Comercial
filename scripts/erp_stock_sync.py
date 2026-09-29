"""Capture every configured eligible deposit; no hardcoded deposit list."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mongo_client import get_db
from sales_coach.services.stock_sync_service import StockSyncService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(ZoneInfo("America/Argentina/Cordoba")).date().isoformat())
    args = parser.parse_args()
    try:
        report = StockSyncService(get_db()).run(args.date)
        print(json.dumps({k: report[k] for k in ("snapshot_id", "status", "execution_coverage", "expected_deposit_ids", "deposits")}, ensure_ascii=False))
        return 0 if report["status"] == "complete" else 2
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
