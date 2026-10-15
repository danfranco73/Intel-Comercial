"""Analyze a completed bounded live capture; output proposals, never database writes.

Usage: python scripts/analyze_sales_phase1.py --capture artifacts/<capture>
All results remain in the private capture directory. Missing evidence fails closed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_sales_phase1 import normalize_erp_sale_row
from clickhouse_client import _compact_records
from sales_coach.domain.sales_v2 import LEGACY_KEY, METRICS, digest, multiset_hash
from scripts.audit_sales_phase1 import number, require, row_fingerprint, reconcile, ZERO, evidence_path
from scripts.capture_sales_phase1 import write_json, validate_page

TAX_FIELDS = ("iva21", "iva27", "iva105", "per3337", "iva2", "percepcion212", "percepcioniibb", "internos")
SOURCE_MONEY = ("subtotalBruto", "subtotalBonificado", "subtotalNeto", *TAX_FIELDS, "subtotalFinal")
SOURCE_QUANTITY = ("cantidadesCorCargo", "cantidadesSinCargo", "cantidadesTotal", "unimedcargo", "unimedscargo", "unimedtotal")
KEY = (*LEGACY_KEY, "company_key")
PATCH_FIELDS = ("amount", "amount_net", "amount_final", "internal_taxes", "amount_net_internal")


def key(row):
    return tuple(str(row.get(k) or "") for k in KEY)


def doc(row):
    return (str(row.get("company_key") or ""), str(row.get("invoice") or ""))


def source_doc(row):
    return tuple(str(row.get(k, "")) for k in ("idEmpresa", "idDocumento", "letra", "serie", "nrodoc"))


def total(rows, field):
    return sum((number(r[field]) for r in rows), ZERO)


def residuals(values):
    return {"rows": len(values), "exact": sum(v == 0 for v in values),
            "within_0_001": sum(abs(v) <= Decimal("0.001") for v in values),
            "within_0_01": sum(abs(v) <= Decimal("0.01") for v in values),
            "max_absolute": max(map(abs, values), default=ZERO), "signed_sum": sum(values, ZERO),
            "absolute_sum": sum(map(abs, values), ZERO)}


def compare_sources(previous, latest):
    """Separate changing master labels from actual economic changes in Chess."""
    identity = lambda r: (*source_doc(r), str(r.get("idLinea", "")))
    old, new = {identity(r): r for r in previous}, {identity(r): r for r in latest}
    require(len(old) == len(previous) and len(new) == len(latest), "Duplicate source identity in drift comparison")
    changed = Counter()
    economic = Counter()
    changed_lines = 0
    missing = object()
    for k in old.keys() & new.keys():
        fields = {f for f in old[k].keys() | new[k].keys() if old[k].get(f, missing) != new[k].get(f, missing)}
        changed_lines += bool(fields)
        changed.update(fields)
        economic.update(fields & set((*SOURCE_MONEY, *SOURCE_QUANTITY, "anulado")))
    return {"old_rows": len(previous), "new_rows": len(latest), "added_identities": len(new.keys()-old.keys()),
            "removed_identities": len(old.keys()-new.keys()), "changed_lines": changed_lines,
            "changed_fields": dict(changed), "economic_or_status_changed_fields": dict(economic)}


def simulate_patch_and_rollback(original, changes):
    """In-memory only. Rollback restores destination-specific types and values."""
    rows = deepcopy(original)
    backup, indexed = {key(r): r for r in original}, {key(r): r for r in rows}
    require(len(indexed) == len(rows), "Non-unique repair key")
    require(len({key(c["key"]) for c in changes}) == len(changes), "Repeated repair key")
    before_hash = multiset_hash(original)
    for change in changes:
        k = key(change["key"])
        require(k in indexed, "Repair key is absent")
        row = indexed[k]
        require(row_fingerprint([row]) == change["before_economic_fingerprint"], "Repair before condition failed")
        require(set(change["after"]) <= set(PATCH_FIELDS), "Repair changes a forbidden field")
        require(all(row[f] == change["before"][f] for f in PATCH_FIELDS), "Repair before values differ")
        for field, value in change["after"].items():
            row[field] = float(number(value))
    projected = {f: total(rows, f) for f in PATCH_FIELDS}
    require(all({k: v for k, v in a.items() if k not in PATCH_FIELDS} ==
                {k: v for k, v in b.items() if k not in PATCH_FIELDS}
                for a, b in zip(original, rows)), "Nonmonetary fields changed")
    for change in changes:
        k = key(change["key"])
        for field in change["after"]:
            indexed[k][field] = backup[k][field]
    require(multiset_hash(rows) == before_hash, "Rollback did not restore full original fingerprint")
    return {"in_memory_only": True, "projected_totals": projected, "rows": len(rows),
            "documents": len({doc(r) for r in rows}), "nonmonetary_fields_unchanged": True,
            "rollback_restores_full_fingerprint": True, "restored_from_destination_specific_backup": True}


def analyze(current, raw, reported_documents):
    normalized, excluded, by_raw_key = [], [], defaultdict(list)
    source_duplicates = Counter((source_doc(r), str(r.get("idLinea"))) for r in raw)
    source_doc_map = defaultdict(set)
    for original in raw:
        row = normalize_erp_sale_row(original)
        if row is None:
            require(original.get("anulado") == "SI", "Invalid non-cancelled source row")
            excluded.append(original)
            continue
        require(all(original.get(f) is not None for f in (*SOURCE_MONEY, *SOURCE_QUANTITY)), "Missing economic or quantity source evidence")
        require(number(row["amount_net"]) == number(original["subtotalNeto"]), "Source net not preserved")
        normalized.append(row)
        by_raw_key[key(row)].append(original)
        source_doc_map[doc(row)].add(source_doc(original))
    require(all(len(v) == 1 for v in source_doc_map.values()), "Distinct source documents collapse into one normalized document")
    replay = _compact_records(normalized)
    a, b = {key(r): r for r in current}, {key(r): r for r in replay}
    require(len(a) == len(current) and len(b) == len(replay), "Duplicate compact keys")
    pairs = [(k, k, "identical_key") for k in sorted(a.keys() & b.keys())]
    only_a, only_b = a.keys() - b.keys(), b.keys() - a.keys()
    dimension_pairs = Counter()
    for field in ("route_description", "channel"):
        idx = KEY.index(field)
        left, right = defaultdict(list), defaultdict(list)
        for k in only_a:
            left[k[:idx] + k[idx+1:]].append(k)
        for k in only_b:
            right[k[:idx] + k[idx+1:]].append(k)
        for reduced in left.keys() & right.keys():
            ka, kb = left[reduced], right[reduced]
            if len(ka) != 1 or len(kb) != 1:
                continue
            ra, rb = a[ka[0]], b[kb[0]]
            if any(abs(number(ra[f]) - number(rb[f])) > (Decimal("0.000001") if f == "quantity" else ZERO) for f in METRICS):
                continue
            pairs.append((ka[0], kb[0], field))
            dimension_pairs[field] += 1
            only_a.remove(ka[0])
            only_b.remove(kb[0])
    zero_only = {k for k in only_b if all(number(b[k][f]) == 0 for f in METRICS)}
    zero_details = []
    for k in sorted(zero_only):
        rr = by_raw_key[k]
        zero_details.append({"key_hash": digest(k), "lines": len(rr),
                             "positive_quantity_lines": sum(number(r["cantidadesTotal"]) > 0 for r in rr),
                             "negative_quantity_lines": sum(number(r["cantidadesTotal"]) < 0 for r in rr),
                             "all_source_money_zero": all(number(r[f]) == 0 for r in rr for f in SOURCE_MONEY)})
    changes, discrepancies, quantity_deltas = [], [], []
    bonus_groups = 0
    for ka, kb, match in sorted(pairs):
        old, new, originals = a[ka], b[kb], by_raw_key[kb]
        qdelta = number(new["quantity"]) - number(old["quantity"])
        quantity_deltas.append(qdelta)
        expected_bonus = -sum((number(r["subtotalBruto"]) for r in originals
                               if number(r["subtotalNeto"]) == 0 and number(r["subtotalBruto"]) != 0), ZERO)
        net_delta = number(new["amount_net"]) - number(old["amount_net"])
        if net_delta:
            bonus_groups += net_delta == expected_bonus
        if any(number(new[f]) - number(old[f]) != (ZERO if f == "internal_taxes" else expected_bonus)
               for f in PATCH_FIELDS):
            discrepancies.append({"key_hash": digest(ka), "reason": "unexplained_money", "net_delta": net_delta})
        if abs(qdelta) > Decimal("0.000001"):
            discrepancies.append({"key_hash": digest(ka), "reason": "quantity_exceeds_compaction_precision", "quantity_delta": qdelta})
        tax = total(originals, "internos")
        net = total(originals, "subtotalNeto")
        final = total(originals, "subtotalFinal")
        proposed = {"amount": final, "amount_net": net, "amount_final": final,
                    "internal_taxes": tax, "amount_net_internal": net + tax}
        changed = {f: proposed[f] for f in PATCH_FIELDS if proposed[f] != number(old[f])}
        if changed:
            changes.append({"key": dict(zip(KEY, ka)), "before": {f: old[f] for f in PATCH_FIELDS},
                            "after": changed, "match_basis": match, "replayed_key_hash": digest(kb),
                            "before_economic_fingerprint": row_fingerprint([old]),
                            "source_hashes": [digest(r) for r in originals],
                            "quantity_unchanged": old["quantity"], "status": "proposal_only_not_applied"})
    included_raw = [r for group in by_raw_key.values() for r in group]
    source_day_docs = defaultdict(set)
    for r in raw:
        source_day_docs[r["fechaComprobate"]].add(source_doc(r))
    docs_check = {day: {"reported": count, "observed": len(source_day_docs[day]),
                       "matches": count == len(source_day_docs[day])} for day, count in reported_documents.items()}
    v1_docs, v2_docs = {doc(r) for r in current}, {doc(r) for r in replay}
    extra_docs = v2_docs - v1_docs
    zero_docs = {doc(b[k]) for k in zero_only}
    by_type = {}
    for typ in sorted({str(r["idDocumento"]) for r in raw}):
        rr = [r for r in raw if str(r["idDocumento"]) == typ]
        inc = [r for r in rr if r.get("anulado") != "SI"]
        exc = [r for r in rr if r.get("anulado") == "SI"]
        by_type[typ] = {"description": rr[0].get("dsDocumento"), "source_rows": len(rr),
                        "included_rows": len(inc), "excluded_rows": len(exc),
                        "included_documents": len({source_doc(r) for r in inc}),
                        "net_included": total(inc, "subtotalNeto"), "net_excluded": total(exc, "subtotalNeto"),
                        "quantity_included": total(inc, "cantidadesTotal"),
                        "negative_net_lines": sum(number(r["subtotalNeto"]) < 0 for r in inc),
                        "negative_quantity_lines": sum(number(r["cantidadesTotal"]) < 0 for r in inc)}
    contract = {
        "source_fields": {f: {"sum_included": total(included_raw, f),
                                "nonzero_rows": sum(number(r[f]) != 0 for r in included_raw)}
                          for f in (*SOURCE_MONEY, *SOURCE_QUANTITY)},
        "gross_minus_discount_minus_net": residuals([number(r["subtotalBruto"]) - number(r["subtotalBonificado"]) - number(r["subtotalNeto"]) for r in included_raw]),
        "final_minus_net_and_taxes": residuals([number(r["subtotalFinal"]) - number(r["subtotalNeto"]) - sum((number(r[f]) for f in TAX_FIELDS), ZERO) for r in included_raw]),
        "total_minus_charged_and_free_quantity": residuals([number(r["cantidadesTotal"]) - number(r["cantidadesCorCargo"]) - number(r["cantidadesSinCargo"]) for r in included_raw]),
        "unimed_total_minus_charged_and_free": residuals([number(r["unimedtotal"]) - number(r["unimedcargo"]) - number(r["unimedscargo"]) for r in included_raw]),
        "internal_taxes_stored": total(current, "internal_taxes"),
        "internal_taxes_source": total(included_raw, "internos"),
        "internal_taxes_current_normalizer": total(replay, "internal_taxes"),
        "quantity_source_used": "cantidadesTotal", "quantity_physical_equivalence_certified": False,
        "source_net_and_final_are_authoritative": True,
        "dimensions_and_quantity_are_not_modified_by_proposal": True,
    }
    per_field = {f: {"rows": sum(f in c["after"] for c in changes),
                      "delta": sum((number(c["after"][f]) - number(c["before"][f]) for c in changes if f in c["after"]), ZERO)} for f in PATCH_FIELDS}
    unexplained_keys = {"only_v1": len(only_a), "only_replay_nonzero": len(only_b - zero_only)}
    counts_explained = not any(unexplained_keys.values()) and extra_docs <= zero_docs and not (v1_docs - v2_docs)
    report = {"money": reconcile(current, raw), "contract": contract,
              "counts": {"raw_rows": len(raw), "cancelled_rows": len(excluded), "included_raw_rows": len(normalized),
                         "v1_compact_rows": len(current), "replayed_compact_rows": len(replay),
                         "compression_removed_rows": len(normalized) - len(replay),
                         "v1_documents": len(v1_docs), "replayed_documents": len(v2_docs),
                         "source_documents_including_cancelled": len({source_doc(r) for r in raw}),
                         "documents_only_replay": len(extra_docs), "extra_documents_are_zero_groups": extra_docs <= zero_docs,
                         "dimension_pairs": dict(dimension_pairs), "zero_groups_only_replay": len(zero_only),
                         "zero_group_source_lines": sum(g["lines"] for g in zero_details),
                         "duplicate_raw_hashes": len(raw) - len({digest(r) for r in raw}),
                         "duplicate_document_line_keys": sum(v-1 for v in source_duplicates.values() if v > 1),
                         "missing_line_identity": sum(r.get("idLinea") in (None, "") for r in raw),
                         "unexplained_keys": unexplained_keys, "counts_explained": counts_explained},
              "source_document_counts": docs_check, "by_document_type": by_type,
              "quantity": {"source_total": total(included_raw, "cantidadesTotal"), "v1_total": total(current, "quantity"),
                           "replayed_total": total(replay, "quantity"), "matched_row_deltas": residuals(quantity_deltas)},
              "unexplained_row_differences": discrepancies, "bonus_groups": bonus_groups,
              "repair_proposal": {"status": "not_applied_requires_review", "rows": len(changes), "fields": per_field,
                                  "zero_group_insertions": 0, "dimension_changes": 0, "quantity_changes": 0},
              "zero_group_details": zero_details}
    return report, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--report-name", default="reconciliation")
    args = parser.parse_args()
    root = args.capture.resolve()
    require(root.is_relative_to(ROOT / "artifacts"), "Private capture directory required")
    require(re.fullmatch(r"[a-zA-Z0-9_-]+", args.report_name), "Invalid report name")
    report_path = root / (args.report_name + ".json")
    proposal = root / (args.report_name + "-repair-proposal.jsonl.gz")
    require(not report_path.exists() and not proposal.exists(), "Refusing to overwrite evidence")
    manifest = json.loads((root / "manifest.json").read_text())
    require(manifest["status"] == "source_verified_databases_unchanged", "Incomplete capture")
    current = [json.loads(s) for s in gzip.open(root / "before-clickhouse.jsonl.gz", "rt")]
    require(row_fingerprint(current) == manifest["before"]["clickhouse_fingerprint"], "Current baseline tampered")
    require(multiset_hash(current) == manifest["before"]["clickhouse_full_fingerprint"], "Full baseline tampered")
    raw, reported, drift = [], {}, {}
    archived = Path.home() / ".local/share/intel-comercial-v2-pilot/days"
    for day in manifest["days"]:
        passes = []
        for run in day["passes"]:
            records, expected = [], None
            for page in run["pages"]:
                body = gzip.open(evidence_path(root, page["path"]), "rb").read()
                require(hashlib.sha256(body).hexdigest() == page["sha256"], "Live capture checksum mismatch")
                rows, expected, documents = validate_page(json.loads(body), day["day"], page["page"], expected)
                records.extend(rows)
                require(documents == run["reported_documents"], "Document count metadata mismatch")
            require(multiset_hash(records) == run["digest"] and len(records) == run["rows"], "Live capture digest mismatch")
            passes.append(records)
        require(len(passes) == 2 and multiset_hash(passes[0]) == multiset_hash(passes[1]), "Two matching passes required")
        raw.extend(passes[0])
        reported[day["day"]] = day["passes"][0]["reported_documents"]
        old_path = archived / f"{day['day']}.json"
        if old_path.exists():
            old = json.loads(old_path.read_text())
            drift[day["day"]] = {"archived_captured_at": old["started_at"],
                                 "same_raw_multiset": old["passes"][0]["digest"] == day["passes"][0]["digest"]}
    report, changes = analyze(current, raw, reported)
    from bson import json_util
    mongo = [json_util.loads(s) for s in gzip.open(root / "before-mongo.jsonl.gz", "rt")]
    require(multiset_hash(mongo) == manifest["before"]["mongo_full_fingerprint"], "Mongo backup tampered")
    simulations = {"clickhouse": simulate_patch_and_rollback(current, changes),
                   "mongo": simulate_patch_and_rollback(mongo, changes)}
    for simulation in simulations.values():
        values = simulation["projected_totals"]
        source = report["contract"]["source_fields"]
        require(values["amount_net"] == source["subtotalNeto"]["sum_included"] and
                values["amount_final"] == values["amount"] == source["subtotalFinal"]["sum_included"] and
                values["internal_taxes"] == source["internos"]["sum_included"] and
                values["amount_net_internal"] == values["amount_net"] + values["internal_taxes"],
                "Proposed totals do not reproduce the source")
    report["repair_simulation"] = simulations
    report.update(range=manifest["range"], direct_chess_capture=True, archived_source_comparison=drift,
                  production_writes=False, source_capture_finished_at=manifest["finished_at"],
                  analysis_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    report["status"] = "differences_explained_not_repaired" if (
        report["money"]["all_monetary_groups_explained"] and report["counts"]["counts_explained"]
        and not report["unexplained_row_differences"] and all(x["matches"] for x in report["source_document_counts"].values())
        and not report["counts"]["duplicate_document_line_keys"] and not report["counts"]["missing_line_identity"]
    ) else "needs_investigation"
    with gzip.open(proposal, "xt") as stream:
        for change in changes:
            stream.write(json.dumps(change, ensure_ascii=False, default=str) + "\n")
    report["repair_proposal"]["sha256"] = hashlib.sha256(proposal.read_bytes()).hexdigest()
    write_json(report_path, report)
    print(json.dumps({"status": report["status"], "counts": report["counts"], "repair_proposal": report["repair_proposal"]}, default=str))


if __name__ == "__main__":
    main()
