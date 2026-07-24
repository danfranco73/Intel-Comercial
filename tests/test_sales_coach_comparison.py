from sales_coach.server import _build_sales_analysis_window


def test_sales_window_accepts_an_explicit_comparison_period():
    window = _build_sales_analysis_window(
        "2026-06-01",
        "2026-06-30",
        "2025-06-01",
        "2025-06-30",
    )

    assert window["selectedStart"] == "2026-06-01"
    assert window["comparisonStart"] == "2025-06-01"
    assert window["comparisonEnd"] == "2025-06-30"
    assert window["loadStart"] == "2025-06-01"
    assert window["loadEnd"] == "2026-06-30"
