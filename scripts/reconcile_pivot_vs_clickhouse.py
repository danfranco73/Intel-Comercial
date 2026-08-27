#!/usr/bin/env python3
"""Reconcilia un export de pivot del ERP (Flexmonster HTML) contra ClickHouse.

Uso:
    .venv/bin/python scripts/reconcile_pivot_vs_clickhouse.py pivotgrid.html \
        --year 2026 --sales-force MERCADERIA

El pivot esperado tiene:
    filtros:  Año = <year>, Descripcion Fuerza Ventas = <sales-force>
    filas:    "Sector de Ventas" (un vendedor / sector por fila)
    columnas: "Nombre Mes Del Año" -> por cada mes las medidas
              [Cantidad Total Bultos, Importe Neto]

Ojo: la plataforma agrupa por `seller_key` (idVendedor de cada comprobante),
mientras que el pivot agrupa por "Sector de Ventas" (dato de maestro). El cruce
por vendedor es orientativo; el cruce por mes y el total son los confiables.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from clickhouse_client import get_clickhouse_client, _qualified_table  # noqa: E402

MONTHS = list(range(1, 13))


# --------------------------------------------------------------------------- #
# Parseo del pivot HTML                                                        #
# --------------------------------------------------------------------------- #
class _PivotParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._row: list[str] = []
        self._cell: list[str] = []
        self._in_cell = False
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td":
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_cell = False
            self._row.append("".join(self._cell).strip())
        elif tag == "tr":
            if self._row:
                self.rows.append(self._row)

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def _num(text: str) -> float | None:
    text = (text or "").strip().replace("\xa0", "")
    if not text or not re.search(r"\d", text):
        return None
    # formato es-AR del pivot: 1,234.567  (coma miles, punto decimal)
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _norm_name(label: str) -> str:
    """'1038 - ROBLES RICHARD KAF' -> 'robles richard kaf'."""
    label = re.sub(r"^\s*\d+\s*-\s*", "", label or "")
    label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", label).strip().lower()


def parse_pivot(path: Path) -> dict:
    parser = _PivotParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))

    # Fila de datos = arranca con un rowHeaderCell de texto y luego pares de números.
    sellers: dict[str, dict] = {}
    totals_row: dict | None = None
    for row in parser.rows:
        if len(row) < 3:
            continue
        label = row[0]
        values = [_num(c) for c in row[1:]]
        numeric = [v for v in values if v is not None]
        if not label or len(numeric) < 4:
            continue
        # esperamos 8 meses * 2 medidas + 2 totales = 18 números
        # (o menos si el año está en curso)
        pairs = values
        months_data: dict[int, dict] = {}
        # tomamos pares (bultos, neto) hasta agotar, dejando los 2 últimos como total
        body = [v for v in pairs]
        # recortar Nones de relleno al final
        while body and body[-1] is None:
            body.pop()
        if len(body) < 4 or len(body) % 2 != 0:
            continue
        *month_vals, tot_bultos, tot_neto = body
        for idx in range(0, len(month_vals) - 1, 2):
            m = idx // 2 + 1
            months_data[m] = {
                "bultos": month_vals[idx] or 0.0,
                "neto": month_vals[idx + 1] or 0.0,
            }
        entry = {
            "label": label,
            "key": _norm_name(label),
            "months": months_data,
            "total_bultos": tot_bultos or 0.0,
            "total_neto": tot_neto or 0.0,
        }
        if _norm_name(label) in {"total", "totales", "grand total"}:
            totals_row = entry
        else:
            sellers[entry["key"]] = entry
    return {"sellers": sellers, "totals": totals_row}


# --------------------------------------------------------------------------- #
# ClickHouse                                                                   #
# --------------------------------------------------------------------------- #
def ch_by_seller_month(year: int, sales_force: str, metric: str) -> dict:
    client = get_clickhouse_client()
    table = _qualified_table()
    q = f"""
        SELECT seller_key, any(seller_name) AS name, month,
               sum({metric}) AS val, sum(quantity) AS qty
        FROM {table}
        WHERE year = %(y)s AND sales_force = %(f)s
        GROUP BY seller_key, month
    """
    out: dict[str, dict] = {}
    for seller_key, name, month, val, qty in client.query(
        q, parameters={"y": year, "f": sales_force}
    ).result_rows:
        key = _norm_name(name) or "sin vendedor"
        entry = out.setdefault(
            key, {"label": name or f"#{seller_key}", "keys": set(), "months": {}}
        )
        entry["keys"].add(seller_key)
        entry["months"][month] = {"val": float(val), "qty": float(qty)}
    return out


def ch_doc_breakdown(year: int, sales_force: str, metric: str) -> list[tuple]:
    client = get_clickhouse_client()
    table = _qualified_table()
    q = f"""
        SELECT month, splitByChar('-', invoice)[1] AS doc,
               count() AS n, sum({metric}) AS val
        FROM {table}
        WHERE year = %(y)s AND sales_force = %(f)s
        GROUP BY month, doc ORDER BY month, val
    """
    return list(client.query(q, parameters={"y": year, "f": sales_force}).result_rows)


# --------------------------------------------------------------------------- #
# Reporte                                                                      #
# --------------------------------------------------------------------------- #
def _fmt(x: float) -> str:
    return f"{x:>18,.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pivot_html", type=Path)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--sales-force", required=True)
    ap.add_argument(
        "--metric",
        default="amount_net",
        choices=["amount_net", "amount", "amount_final", "amount_net_internal"],
        help="Campo de ClickHouse a comparar contra 'Importe Neto' del pivot (default amount_net).",
    )
    args = ap.parse_args()

    pivot = parse_pivot(args.pivot_html)
    if not pivot["sellers"]:
        print("No se pudo parsear ninguna fila del pivot.", file=sys.stderr)
        return 1

    months_present = sorted(
        {m for s in pivot["sellers"].values() for m in s["months"]}
    )
    ch = ch_by_seller_month(args.year, args.sales_force, args.metric)

    # ---- Comparación por mes (total fuerza) -------------------------------- #
    print(f"\n=== {args.sales_force} {args.year} — comparación por mes ===")
    print(f"metric ClickHouse = {args.metric}\n")
    print(f"{'mes':>3} {'ERP (pivot)':>18} {'plataforma (CH)':>18} {'dif':>16} {'dif%':>8}   {'ERP bultos':>13} {'CH bultos':>13}")
    ch_month_val = {m: 0.0 for m in MONTHS}
    ch_month_qty = {m: 0.0 for m in MONTHS}
    for entry in ch.values():
        for m, d in entry["months"].items():
            ch_month_val[m] += d["val"]
            ch_month_qty[m] += d["qty"]
    tot_erp = tot_ch = tot_erp_q = tot_ch_q = 0.0
    for m in months_present:
        erp = sum(s["months"].get(m, {}).get("neto", 0.0) for s in pivot["sellers"].values())
        erp_q = sum(s["months"].get(m, {}).get("bultos", 0.0) for s in pivot["sellers"].values())
        chm = ch_month_val.get(m, 0.0)
        chm_q = ch_month_qty.get(m, 0.0)
        dif = chm - erp
        pct = (100 * dif / erp) if erp else 0.0
        print(f"{m:>3} {_fmt(erp)} {_fmt(chm)} {dif:>16,.2f} {pct:>7.2f}%   {erp_q:>13,.1f} {chm_q:>13,.1f}")
        tot_erp += erp
        tot_ch += chm
        tot_erp_q += erp_q
        tot_ch_q += chm_q
    dif = tot_ch - tot_erp
    pct = (100 * dif / tot_erp) if tot_erp else 0.0
    print(f"{'TOT':>3} {_fmt(tot_erp)} {_fmt(tot_ch)} {dif:>16,.2f} {pct:>7.2f}%   {tot_erp_q:>13,.1f} {tot_ch_q:>13,.1f}")

    # ---- Comparación por vendedor / sector ------------------------------- #
    print(f"\n=== por vendedor/sector (cruce por apellido, orientativo) ===")
    print(f"{'ERP (pivot)':<32} {'ERP neto':>16} {'CH neto':>16} {'dif':>15}   CH keys")
    matched_ch = set()
    for key, s in sorted(pivot["sellers"].items(), key=lambda kv: -kv[1]["total_neto"]):
        erp_tot = sum(s["months"].get(m, {}).get("neto", 0.0) for m in months_present)
        cand = ch.get(key)
        ch_tot = 0.0
        keys_txt = "-"
        if cand:
            matched_ch.add(key)
            ch_tot = sum(cand["months"].get(m, {}).get("val", 0.0) for m in months_present)
            keys_txt = ",".join(sorted(cand["keys"]))
        print(f"{s['label'][:32]:<32} {erp_tot:>16,.2f} {ch_tot:>16,.2f} {ch_tot - erp_tot:>15,.2f}   {keys_txt}")

    extra = [v for k, v in ch.items() if k not in matched_ch]
    if extra:
        print(f"\n--- vendedores en la plataforma SIN fila equivalente en el pivot ---")
        for v in sorted(extra, key=lambda e: -sum(d['val'] for d in e['months'].values())):
            tot = sum(d["val"] for d in v["months"].values())
            print(f"{v['label'][:32]:<32} {tot:>16,.2f}   keys={','.join(sorted(v['keys']))}")

    # ---- Devoluciones / notas de crédito por mes ------------------------- #
    print(f"\n=== tipos de comprobante en la plataforma (CH), por mes ===")
    print("(DVVTA/PRDVO = devoluciones. Si faltan en meses viejos, esos meses quedan inflados)")
    for month, doc, n, val in ch_doc_breakdown(args.year, args.sales_force, args.metric):
        print(f"  mes {month:>2}  {doc:<8} filas={n:>7}  {val:>18,.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
