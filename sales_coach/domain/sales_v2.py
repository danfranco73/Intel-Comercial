"""Pilot-only, lossless logistics normalization. No production registration."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json

VERSION = "sales-v2-pilot-1"
METRICS = ("amount", "amount_net", "amount_final", "internal_taxes", "amount_net_internal", "quantity")
LEGACY_KEY = ("date", "client_key", "invoice", "product_key", "seller_key", "sales_scheme_key", "route_description", "channel")
QUANTITIES = ("cantidadesTotal", "cantidadesCorCargo", "cantidadesSinCargo", "cantidadesRechazo",
              "unimedtotal", "unimedcargo", "unimedscargo", "cantidadSolicitada", "unidadesSolicitadas", "peso", "pesoTotal")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def multiset_hash(rows):
    # Preserve multiplicities; source ordering is not an identity guarantee.
    return digest(sorted(Counter(digest(r) for r in rows).items()))


def identifier(value):
    if isinstance(value, bool) or value is None or not str(value).strip().isdigit():
        return None
    return str(int(str(value))) if int(str(value)) > 0 else None


def number(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def normalize(raw, *, capture_id, page, ordinal, captured_at):
    from erp_client import normalize_erp_sale_row
    legacy = normalize_erp_sale_row(raw)
    doc_fields = ("idEmpresa", "idDocumento", "letra", "serie", "nrodoc")
    doc = [raw.get(k) for k in doc_fields]
    doc_key = canonical(doc) if all(v not in (None, "") for v in (doc[0], doc[1], doc[3], doc[4])) else None
    line = raw.get("idLinea")
    quantities = {k: raw.get(k) for k in QUANTITIES}
    return {
        "capture_id": capture_id, "page": page, "ordinal": ordinal, "captured_at": captured_at,
        "normalizer_version": VERSION, "raw_hash": digest(raw), "raw": raw,
        "date": raw.get("fechaComprobate"), "idDeposito": identifier(raw.get("idDeposito")),
        "dsDeposito": raw.get("dsDeposito"), "planillaCarga": raw.get("planillaCarga"),
        "route_description": raw.get("desRuta"), "idEmpresa": identifier(raw.get("idEmpresa")),
        "idArticulo": identifier(raw.get("idArticulo")),
        "idArticuloEstadistico": identifier(raw.get("idArticuloEstadistico")),
        "document_identity": doc_key,
        "line_identity": canonical([doc, line]) if doc_key and line not in (None, "") else None,
        "quantities": quantities, "commercial_included": legacy is not None,
        "exclusion": None if legacy is not None else "cancelled" if raw.get("anulado") == "SI" else "invalid_commercial_row",
        "legacy": legacy,
    }


def equivalent_key(row):
    return tuple(str(row.get(k) or "") for k in LEGACY_KEY)


def reconcile(baseline, candidate, tolerance=Decimal("0.000001")):
    """Compare compact-equivalent rows, including duplicate keys and entity sets."""
    def indexed(rows):
        groups = {}
        for r in rows:
            groups.setdefault(equivalent_key(r), []).append(r)
        return groups
    a, b = indexed(baseline), indexed(candidate)
    differences = []
    for key in sorted(a.keys() | b.keys()):
        left, right = a.get(key, []), b.get(key, [])
        if len(left) != 1 or len(right) != 1:
            differences.append({"key": key, "type": "missing_or_duplicate", "v1_rows": len(left), "v2_rows": len(right)})
            continue
        delta = {m: sum(number(r.get(m)) or Decimal(0) for r in right) - sum(number(r.get(m)) or Decimal(0) for r in left) for m in METRICS}
        attributes = [k for k in ("company_key", "supplier_key", "business_type_key") if str(left[0].get(k) or "") != str(right[0].get(k) or "")]
        if any(abs(x) > tolerance for x in delta.values()) or attributes:
            differences.append({"key": key, "type": "values", "delta": {k: str(v) for k,v in delta.items()}, "attributes": attributes})
    def summary(rows):
        result = {"rows": len(rows), "metrics": {m: str(sum((number(r.get(m)) or Decimal(0) for r in rows), Decimal(0))) for m in METRICS}}
        for key in ("client_key", "product_key", "seller_key"):
            result[key] = sorted({str(r.get(key) or "") for r in rows})
        result["documents"] = sorted({canonical([r.get("company_key"), r.get("invoice")]) for r in rows})
        return result
    sa, sb = summary(baseline), summary(candidate)
    entity_equal = {k: sa[k] == sb[k] for k in ("client_key", "product_key", "seller_key", "documents")}
    for s in (sa, sb):
        for k in ("client_key", "product_key", "seller_key", "documents"):
            s[k] = {"count": len(s[k]), "sha256": digest(s[k])}
    return {"consistent": not differences and all(entity_equal.values()), "baseline": sa, "candidate": sb,
            "entity_sets_equal": entity_equal, "difference_count": len(differences), "differences": differences}


def unit_evidence(rows, uxb, *, pesable=False, combo=False):
    """Observed numerical conversion, not a claim of historical master validity."""
    u = number(uxb)
    reasons = []
    if not u or u <= 0:
        reasons.append("UXB_MISSING")
    elif u != u.to_integral_value():
        reasons.append("UXB_NOT_INTEGER")
    if pesable:
        reasons.append("WEIGHABLE")
    if combo:
        reasons.append("COMBO")
    checked = 0
    for row in rows:
        packs = number(row.get("cantidadesTotal"))
        closed, loose = number(row.get("cantidadSolicitada")), number(row.get("unidadesSolicitadas"))
        presentation = number(row.get("presentacionArticulo"))
        if presentation != u:
            reasons.append("HISTORICAL_PRESENTATION_MISMATCH")
        if packs is None or closed is None or loose is None:
            reasons.append("QUANTITY_MISSING")
        elif u and u > 0:
            if closed != closed.to_integral_value() or loose != loose.to_integral_value():
                reasons.append("NONINTEGER_PHYSICAL_PARTS")
            units = closed*u+loose
            # Source packs may be rounded to six decimals; units must still agree.
            if abs(packs * u - units) > max(Decimal("0.000001"), u * Decimal("0.0000005")):
                reasons.append("UXB_RATIO_MISMATCH")
            elif packs != 0:
                checked += 1
    if not checked:
        reasons.append("NO_NONZERO_EVIDENCE")
    return {"observed_compatible": not reasons, "checked_nonzero_rows": checked,
            "uxb": str(u) if u else None, "reasons": sorted(set(reasons)),
            "historical_master_validity_certified": False}
