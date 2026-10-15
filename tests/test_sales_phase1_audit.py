"""Synthetic audit-contract tests; these are not evidence of business results."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from scripts.audit_sales_phase1 import dates, evidence_path, reconcile, row_fingerprint
from clickhouse_client import _compact_records
from erp_client import normalize_erp_sale_row


def source(**changes):
    return {"fechaComprobate": "2026-09-22", "idCliente": 1, "idEmpresa": 1,
            "idDocumento": "FCVTA", "letra": "A", "serie": 1, "nrodoc": 5,
            "idArticulo": 12, "subtotalNeto": 0, "subtotalFinal": 0,
            "subtotalBruto": 100, "subtotalBonificado": 100, "bonificacion": 100,
            "cantidadesTotal": 2, "cantidadesSinCargo": 2, "cantidadesCorCargo": 0,
            "anulado": "NO", **changes}


def persisted(raw, inflation=0):
    from scripts.audit_sales_phase1 import normalize_erp_sale_row as captured_v1
    row = _compact_records([captured_v1(raw)])[0]
    for field in ["amount", "amount_net", "amount_final", "amount_net_internal"]:
        row[field] += inflation
    return row


def test_bonus_and_return_preserve_zero_and_signed_quantities():
    raw = [source(), source(idDocumento="DVVTA", subtotalBruto=-40,
                            subtotalBonificado=-40, cantidadesTotal=-1, cantidadesSinCargo=-1)]
    before = [persisted(raw[0], 100), persisted(raw[1], -40)]
    original = deepcopy(before)
    result = reconcile(before, raw)
    assert result["all_monetary_groups_explained"]
    assert result["explained_net_delta"] == Decimal(-60)
    assert result["net_replayed_source"] == 0
    assert result["bonus_returns"] == 1
    assert before == original


def test_opposite_errors_in_different_companies_cannot_cancel():
    raw = [source(subtotalNeto=10, subtotalFinal=12, idEmpresa=1),
           source(subtotalNeto=10, subtotalFinal=12, idEmpresa=2, nrodoc=6)]
    result = reconcile([persisted(raw[0], 5), persisted(raw[1], -5)], raw)
    assert not result["all_monetary_groups_explained"]
    assert result["net_v1"] == result["net_replayed_source"]


def test_missing_source_net_is_never_zero_or_a_verified_bonus():
    raw = source(subtotalNeto=None)
    with pytest.raises(ValueError, match="Missing numeric"):
        reconcile([persisted(source())], [raw])


def test_cancelled_lines_excluded_but_unexpected_invalid_rows_fail():
    result = reconcile([], [source(anulado="SI")])
    assert result["excluded_cancelled_rows"] == 1
    with pytest.raises(ValueError, match="Unexpected normalization"):
        reconcile([], [source(idCliente=None)])


def test_duplicate_v1_keys_rejected():
    raw = source()
    row = persisted(raw, 100)
    with pytest.raises(ValueError, match="Duplicate current V1"):
        reconcile([row, row], [raw])


def test_fingerprints_keep_multiplicity_and_ignore_numeric_and_date_encoding():
    row = persisted(source(), 100)
    encoded = {**row, "date": "2026-09-22", "amount_net": 100}
    assert row["date"] == date(2026, 9, 22)
    assert row_fingerprint([row]) == row_fingerprint([encoded])
    assert row_fingerprint([row]) != row_fingerprint([row, row])


def test_scope_is_bounded_and_archive_paths_cannot_escape(tmp_path):
    assert len(dates("2026-09-22", "2026-09-28")) == 7
    for start, end in [("2026-09-21", "2026-09-28"), ("2026-09-28", "2026-09-22")]:
        with pytest.raises(ValueError, match="one and seven"):
            dates(start, end)
    with pytest.raises(ValueError, match="escapes archive"):
        evidence_path(tmp_path, "../raw.json")


def test_live_pagination_does_not_treat_short_detail_page_as_complete():
    from scripts.capture_sales_phase1 import validate_page
    payload = {"cantComprobantesVentas": "Numero de lote obtenido: 1/2. Cantidad de comprobantes totales: 1200",
               "dsReporteComprobantesApi": {"VentasResumen": [source()]}}
    rows, pages, documents = validate_page(payload, "2026-09-22", 1)
    assert len(rows) == 1 and pages == 2 and documents == 1200
    with pytest.raises(ValueError, match="Pagination changed"):
        validate_page(payload, "2026-09-22", 1, expected_pages=3)
    with pytest.raises(ValueError, match="outside requested"):
        validate_page(payload, "2026-09-23", 1)


def test_live_empty_response_requires_explicit_document_count():
    from scripts.capture_sales_phase1 import validate_page
    payload = {"cantComprobantesVentas": "Numero de lote obtenido: 0/0. Cantidad de comprobantes totales: 0",
               "dsReporteComprobantesApi": {}}
    assert validate_page(payload, "2026-09-22", 1) == ([], 0, 0)
    payload["cantComprobantesVentas"] = "Numero de lote obtenido: 1/1. Cantidad de comprobantes totales: 20"
    payload["dsReporteComprobantesApi"] = {"VentasResumen": []}
    with pytest.raises(ValueError, match="Unconfirmed empty"):
        validate_page(payload, "2026-09-22", 1)


def complete_source(**changes):
    from scripts.analyze_sales_phase1 import TAX_FIELDS
    return source(**{**{f: 0 for f in TAX_FIELDS}, "idLinea": 1,
                     "unimedcargo": 0, "unimedscargo": 24, "unimedtotal": 24, **changes})


def test_live_analysis_detects_internos_mapping_without_changing_quantity_or_keys():
    from scripts.analyze_sales_phase1 import analyze
    raw = complete_source(subtotalNeto=80, subtotalBonificado=20, bonificacion=20,
                          subtotalFinal=100.4, iva21=16.8, internos=3.6)
    before = persisted(raw)
    report, changes = analyze([before], [raw], {"2026-09-22": 1})
    assert report["contract"]["internal_taxes_stored"] == 0
    assert report["contract"]["internal_taxes_source"] == Decimal("3.6")
    assert report["contract"]["final_minus_net_and_taxes"]["max_absolute"] == 0
    assert changes[0]["after"] == {"internal_taxes": Decimal("3.6"), "amount_net_internal": Decimal("83.6")}
    assert changes[0]["quantity_unchanged"] == before["quantity"]
    assert report["counts"]["counts_explained"]
    assert report["source_document_counts"]["2026-09-22"]["matches"]


def test_counts_reconcile_zero_documents_and_dimension_changes_separately():
    from scripts.analyze_sales_phase1 import analyze
    paid = complete_source(subtotalNeto=10, subtotalFinal=12.1, subtotalBruto=10,
                           subtotalBonificado=0, iva21=2.1, desRuta="Ruta actual")
    zero = complete_source(nrodoc=6, subtotalBruto=0, subtotalBonificado=0)
    reverse = {**zero, "idLinea": 2, "cantidadesTotal": -2, "cantidadesSinCargo": -2,
               "unimedscargo": -24, "unimedtotal": -24}
    before = persisted(paid)
    before["route_description"] = "Ruta anterior"
    report, changes = analyze([before], [paid, zero, reverse], {"2026-09-22": 2})
    assert report["counts"]["dimension_pairs"] == {"route_description": 1}
    assert report["counts"]["zero_groups_only_replay"] == 1
    assert report["counts"]["zero_group_source_lines"] == 2
    assert report["counts"]["documents_only_replay"] == 1
    assert report["counts"]["counts_explained"]
    assert changes == []  # No automatic row insertions or reassignment.


def test_live_analysis_rejects_missing_tax_evidence():
    from scripts.analyze_sales_phase1 import analyze
    raw = complete_source(internos=None)
    with pytest.raises(ValueError, match="Missing economic"):
        analyze([persisted(raw)], [raw], {"2026-09-22": 1})


def test_document_counts_and_line_duplicates_are_not_hidden_by_money_agreement():
    from scripts.analyze_sales_phase1 import analyze
    raw = complete_source()
    current = _compact_records([normalize_erp_sale_row(raw), normalize_erp_sale_row(raw)])
    report, _ = analyze(current, [raw, raw], {"2026-09-22": 2})
    assert report["counts"]["duplicate_document_line_keys"] == 1
    assert not report["source_document_counts"]["2026-09-22"]["matches"]


def test_source_drift_distinguishes_descriptions_from_money_and_missing_lines():
    from scripts.analyze_sales_phase1 import compare_sources
    old = complete_source(nombreCliente="Anterior")
    changed = {**old, "nombreCliente": "Actual"}
    report = compare_sources([old], [changed])
    assert report["changed_fields"] == {"nombreCliente": 1}
    assert report["economic_or_status_changed_fields"] == {}
    changed["subtotalNeto"] = 1
    assert compare_sources([old], [changed])["economic_or_status_changed_fields"] == {"subtotalNeto": 1}
    assert compare_sources([old], [])["removed_identities"] == 1


def test_rollback_uses_mongo_original_types_not_clickhouse_before_types():
    from scripts.analyze_sales_phase1 import analyze, simulate_patch_and_rollback
    raw = complete_source(subtotalNeto=80, subtotalBonificado=20, bonificacion=20,
                          subtotalFinal=100.4, iva21=16.8, internos=3.6)
    clickhouse = persisted(raw)
    _, changes = analyze([clickhouse], [raw], {"2026-09-22": 1})
    mongo = {**clickhouse, "internal_taxes": 0, "_id": "immutable-synthetic-id"}
    assert type(changes[0]["before"]["internal_taxes"]) is float
    result = simulate_patch_and_rollback([mongo], changes)
    assert result["rollback_restores_full_fingerprint"]
    assert type(mongo["internal_taxes"]) is int
    assert mongo["_id"] == "immutable-synthetic-id"


def test_repair_simulation_rejects_quantity_changes_and_stale_before_values():
    from scripts.analyze_sales_phase1 import analyze, simulate_patch_and_rollback
    raw = complete_source()
    before = persisted(raw, 100)
    _, changes = analyze([before], [raw], {"2026-09-22": 1})
    bad = deepcopy(changes)
    bad[0]["after"]["quantity"] = 999
    with pytest.raises(ValueError, match="forbidden field"):
        simulate_patch_and_rollback([before], bad)
    with pytest.raises(ValueError, match="before condition"):
        simulate_patch_and_rollback([{**before, "amount_net": 101}], changes)
