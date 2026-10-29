"""Compara el informe por totales (SQL en ClickHouse) contra el cruce en Python
sobre las mismas filas de ClickHouse, para un período corto (un mes cerrado).

Uso: python scripts/validate_series_clickhouse.py 2026-09-01 2026-09-30

Usa las credenciales del .env. Única escritura: crea/llena dim_articles y
dim_articles_staging (copia del maestro de artículos de Mongo), igual que la app
la primera vez que se pide una serie. El resto son consultas de lectura.
Trae las filas del período a memoria para el cruce en Python: no usar con
rangos largos.
"""
import sys
import time
from collections import defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from analyzer import dimension_value, metric_value
from bi.facts import enrich_sales_records
from clickhouse_client import build_clickhouse_series_report, load_erp_sales_dataset_clickhouse, get_clickhouse_storage_status
from mongo_client import get_erp_storage_status, load_erp_articles_dataset

if len(sys.argv) != 3:
    sys.exit(__doc__)
START, END = sys.argv[1], sys.argv[2]

storage = get_erp_storage_status()
print("clickhouse coverage:", {k: v for k, v in get_clickhouse_storage_status().items() if k in ("periodStart", "periodEnd", "rows", "lastSyncAt")})
articles = load_erp_articles_dataset()["records"]
print("articles:", len(articles), "version:", storage.get("articlesLastSyncAt"))

t0 = time.perf_counter()
report = build_clickhouse_series_report(START, END, {}, articles_version=storage.get("articlesLastSyncAt"), load_articles=lambda: articles)
sql_seconds = time.perf_counter() - t0
t0 = time.perf_counter()
report_again = build_clickhouse_series_report(START, END, {"channel": [report["interactiveReport"]["byDimension"]["channel"]["amount_net"][0]["name"]]},
                                             articles_version=storage.get("articlesLastSyncAt"), load_articles=lambda: articles)
filtered_seconds = time.perf_counter() - t0

t0 = time.perf_counter()
rows = load_erp_sales_dataset_clickhouse(START, END)["records"]
load_seconds = time.perf_counter() - t0
enriched = enrich_sales_records(rows, {"articles": {"records": articles}})
names = {a["product_key"]: (a.get("product_name") or a["product_key"]) for a in articles}
for item in enriched:
    item["product_name"] = names.get(item["product_key"], "Sin producto")
python_seconds = time.perf_counter() - t0

SUMS = ["quantity", "amount_net", "amount_final", "amount_net_internal"]
ref_sums = {m: sum(metric_value(i, m) for i in enriched) for m in SUMS}
ref_buyers = len({i["client_canonical"] for i in enriched})
ref_orders = len({i["invoice"] for i in enriched if i.get("invoice")})

def close(a, b):
    return abs(a - b) <= max(0.01, abs(b) * 1e-9)

summary = report["summary"]
checks = [
    ("venta neta", summary["salesCurrent"], ref_sums["amount_net"]),
    ("bultos", summary["unitsCurrent"], ref_sums["quantity"]),
    ("venta final", summary["salesFinalCurrent"], ref_sums["amount_final"]),
    ("pedidos", summary["ordersCurrent"], ref_orders),
    ("clientes compradores", summary["activeClients"], ref_buyers),
]
failures = 0
for label, ours, ref in checks:
    ok = close(float(ours), float(ref))
    failures += not ok
    print(f"{'OK ' if ok else 'DIF'} {label}: sql={ours} python={round(ref, 2)}")

for field in ["family", "line", "brand", "caliber", "supplier", "channel", "product_name"]:
    ref = defaultdict(lambda: {"amount_net": 0.0, "quantity": 0.0, "buyers": set()})
    for item in enriched:
        bucket = ref[dimension_value(item, field)]
        bucket["amount_net"] += metric_value(item, "amount_net")
        bucket["quantity"] += metric_value(item, "quantity")
        bucket["buyers"].add(item["client_canonical"])
    series = report["interactiveReport"]["byDimension"][field]
    bad = []
    for metric in ("amount_net", "quantity", "buyers"):
        for row in series[metric]:
            expected = len(ref[row["name"]]["buyers"]) if metric == "buyers" else ref[row["name"]][metric]
            if not close(float(row["total"]), float(expected)):
                bad.append((metric, row["name"], row["total"], round(expected, 2)))
    top_ref = sorted(ref.items(), key=lambda pair: pair[1]["amount_net"], reverse=True)[:12]
    top_sql = [row["name"] for row in series["amount_net"]]
    same_top = [name for name, _ in top_ref] == top_sql
    failures += bool(bad) + (not same_top)
    print(f"{'OK ' if not bad and same_top else 'DIF'} {field}: {len(ref)} valores, top12 igual={same_top}, diferencias={bad[:3]}")

print(f"filas del mes: {len(rows)}")
print(f"tiempos: SQL totales {sql_seconds:.2f}s · SQL con filtro {filtered_seconds:.2f}s · lectura de filas {load_seconds:.2f}s · cruce Python {python_seconds:.2f}s")
print("RESULTADO:", "todo coincide" if not failures else f"{failures} diferencias")
