from copy import deepcopy
import gc
import tracemalloc

import pytest

from analyzer import analyze_datasets, build_tactical_month_report
from bi.facts import enrich_sales_records
from sales_coach.server import _build_sales_analysis_window


@pytest.mark.parametrize("report", [analyze_datasets, build_tactical_month_report])
@pytest.mark.parametrize("options", [{}, {"filters": {"seller_name": ["Vendedora Norte"]}},
                                    {"scope_filters": {"sales_scheme_name": ["Minorista"]}}])
def test_consumed_reports_preserve_entire_response(datasets, report, options):
    # Same selected dates as the incident, with a separate comparative month.
    for row in datasets["sales"]["records"]:
        row["date"] = row["date"].replace(month=row["date"].month + 4)
        row["month"] = row["date"].month
    datasets["sales"]["analysisRange"] = {"fechaDesde": "2026-10-01", "fechaHasta": "2026-10-29"}
    datasets["sales"]["comparisonRange"] = {"fechaDesde": "2026-09-02", "fechaHasta": "2026-09-30"}
    expected = report(deepcopy(datasets), **options)
    actual = report(datasets, release_raw_sales=True, **options)
    assert actual == expected  # Includes quantities, amounts, filters, metadata and comparison.
    assert datasets["sales"]["records"] == []


def test_consumption_preserves_rows_order_returns_and_shared_input(sales_records):
    sales_records.append({**sales_records[-1], "quantity": -2.5, "amount_net": -12.5})
    original = deepcopy(sales_records)
    shared_row = sales_records[0]
    expected = enrich_sales_records(sales_records, {})
    assert sales_records == original
    actual = enrich_sales_records(sales_records, {}, consume=True)
    assert actual == expected
    assert shared_row == original[0]
    assert actual[-1]["quantity"] == -2.5
    assert actual[-1]["amount"] == -12.5
    assert sales_records == []


def test_incident_comparison_window():
    window = _build_sales_analysis_window("2026-10-01", "2026-10-29")
    assert window["days"] == 29
    assert window["comparisonStart"] == "2026-09-02"
    assert window["comparisonEnd"] == "2026-09-30"


def test_consuming_reduces_enrichment_peak(sales_records):
    def measure(consume):
        gc.collect()
        tracemalloc.start()
        try:
            rows = [dict(sales_records[i % len(sales_records)]) for i in range(6000)]
            enriched = enrich_sales_records(rows, {}, consume=consume)
            rows.clear()  # Previous implementation released only after enrichment.
            peak = tracemalloc.get_traced_memory()[1]
            assert len(enriched) == 6000
            totals = (sum(r["amount"] for r in enriched), sum(r["quantity"] for r in enriched))
            return peak, totals
        finally:
            tracemalloc.stop()

    before, before_totals = measure(False)
    after, after_totals = measure(True)
    print(f"synthetic enrichment peak: before={before} after={after} reduction={1-after/before:.1%}")
    assert before_totals == after_totals
    assert after < before * 0.85
