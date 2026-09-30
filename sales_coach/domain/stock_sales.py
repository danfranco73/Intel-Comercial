"""Preparation contract only: no inventory policy, velocity or coverage calculation."""
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class DailyProductSale:
    day: date
    statistical_article_id: str
    quantity: float | None
    quantity_unit: str | None
    ingestion_coverage: str
    evidence_ids: tuple[str, ...]


class DailyProductHistoryProvider(Protocol):
    def daily(self, *, statistical_article_id: str, start: date, end: date,
              scope_id: str) -> list[DailyProductSale]: ...


@dataclass(frozen=True)
class StockSalesCompatibility:
    snapshot_id: str
    identity_version: str | None
    physical_article_id: str
    statistical_article_id: str | None
    identity_status: str
    sales_coverage_status: str
    stock_execution_coverage: str
    # A validated pair of comparable scopes is necessary even for group totals.
    scope_equivalence_verified: bool = False
    quantity_equivalence_verified: bool = False
    temporal_alignment_verified: bool = False

    def readiness(self):
        reasons = []
        if self.identity_status != "verified" or not self.statistical_article_id or not self.identity_version:
            reasons.append("PRODUCT_IDENTITY_UNVERIFIED")
        if self.sales_coverage_status != "complete":
            reasons.append("SALES_HISTORY_UNVERIFIED")
        if self.stock_execution_coverage != "complete":
            reasons.append("STOCK_CAPTURE_INCOMPLETE")
        if not self.scope_equivalence_verified:
            reasons.append("STOCK_SALES_SCOPE_UNVERIFIED")
        if not self.quantity_equivalence_verified:
            reasons.append("QUANTITY_EQUIVALENCE_UNVERIFIED")
        if not self.temporal_alignment_verified:
            reasons.append("TEMPORAL_ALIGNMENT_UNVERIFIED")
        return {"join_ready": not reasons, "blocking_reasons": reasons,
                "velocity": None, "days_of_supply": None, "business_rules_enabled": False}
