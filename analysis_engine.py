"""
analysis_engine.py — Fase 3 del motor dinámico de análisis comercial.

Responsabilidad:
    Ejecutar cualquier AnalysisTask (producido por rule_engine) sobre un
    conjunto de registros y devolver datos estructurados (sin frases,
    sin colores, sin lógica de presentación).

    Completamente agnóstico a nombres de columnas: opera sobre los
    nombres de campo reales resueltos en task["fields"].

    Diseño:
      - AnalysisEngine.run_task(task, records)
          → ejecuta el método correspondiente al task["id"]
      - AnalysisEngine.run_all(tasks, records)
          → ejecuta todos los tasks y devuelve {task_id: result}
      - AnalysisEngine.run_task_with_combo(task, records, combo)
          → ejecuta un task filtrando/agrupando por un combo específico
            (usado por el endpoint dinámico /api/analyze-dynamic)

Resultados normalizados:
    Cada método devuelve un dict con estructura fija documentada en su
    docstring. Esas estructuras son la entrada de kpi_generator (Fase 4),
    viz_selector (Fase 4) e insight_writer (Fase 5).

Compatibilidad:
    Este módulo NO reemplaza analyze_datasets(). Convive con él.
    analyze_datasets() llama a run_all() y agrega "engineResults" al JSON.
    La migración gradual se completa en la Fase 6 (frontend dinámico).
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median


# ---------------------------------------------------------------------------
# Valores sentinela: se excluyen de los cálculos (no son datos reales)
# ---------------------------------------------------------------------------
_MISSING = frozenset({
    "Sin vendedor", "Sin ruta", "Sin familia", "Sin canal",
    "Sin fuerza de ventas", "Sin proveedor", "Sin línea",
    "Sin sabor", "Sin UxB", "Sin calibre", "Sin producto", "Sin artículo",
    None, "", False,
})


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------

class AnalysisEngine:

    # ----------------------------------------------------------------
    # Orquestadores públicos
    # ----------------------------------------------------------------

    def run_all(self, tasks, records):
        """
        Ejecuta todos los tasks con sus campos primarios (sin combo).

        Returns:
            dict[task_id -> result_dict]
        """
        results = {}
        for task in tasks:
            try:
                results[task["id"]] = self.run_task(task, records)
            except Exception as exc:
                results[task["id"]] = {"error": str(exc)}
        return results

    def run_task(self, task, records):
        """
        Ejecuta un AnalysisTask sobre records usando los campos primarios.
        Para dimension_ranking usa la primera dimensión del schema como fallback.
        """
        dispatch = {
            "temporal_trend":        self._run_temporal_trend,
            "dimension_ranking":     self._run_dimension_ranking,
            "recurrence_churn":      self._run_recurrence_churn,
            "cross_sell_mix":        self._run_cross_sell_mix,
            "seller_performance":    self._run_seller_performance,
            "geographic_coverage":   self._run_geographic_coverage,
            "margin_analysis":       self._run_margin_analysis,
            "sales_force_breakdown": self._run_sales_force_breakdown,
            "product_analysis":      self._run_product_analysis,
            "channel_analysis":      self._run_channel_analysis,
        }
        fn = dispatch.get(task["id"])
        if fn is None:
            return {"error": f"Análisis '{task['id']}' no implementado"}
        # dimension_ranking sin combo: usar primera dimensión del primer combo disponible
        if task["id"] == "dimension_ranking":
            first_combo = task["combos"][0] if task.get("combos") else None
            return fn(task["fields"], records, dim_field=None, combo=first_combo)
        return fn(task["fields"], records, dim_field=None)

    def run_task_with_combo(self, task, records, combo):
        """
        Ejecuta un task con un ComboSpec específico (del rule_engine).
        dim_a es la dimensión principal; dim_b (opcional) permite cruce.

        Args:
            task:   AnalysisTask
            records: list[dict]
            combo:  {"dim_a": "seller_name", "dim_b": "sales_force" | None, ...}

        Returns:
            dict — igual que run_task pero segmentado/filtrado por combo
        """
        dispatch = {
            "temporal_trend":        self._run_temporal_trend,
            "dimension_ranking":     self._run_dimension_ranking,
            "recurrence_churn":      self._run_recurrence_churn,
            "cross_sell_mix":        self._run_cross_sell_mix,
            "seller_performance":    self._run_seller_performance,
            "geographic_coverage":   self._run_geographic_coverage,
            "margin_analysis":       self._run_margin_analysis,
            "sales_force_breakdown": self._run_sales_force_breakdown,
            "product_analysis":      self._run_product_analysis,
            "channel_analysis":      self._run_channel_analysis,
        }
        fn = dispatch.get(task["id"])
        if fn is None:
            return {"error": f"Análisis '{task['id']}' no implementado"}
        dim_a = combo.get("dim_a") if combo else None
        return fn(task["fields"], records, dim_field=dim_a, combo=combo)

    # ----------------------------------------------------------------
    # Temporal trend
    # ----------------------------------------------------------------

    def _run_temporal_trend(self, fields, records, dim_field=None, combo=None):
        """
        Evolución temporal de una métrica, opcionalmente desagregada por dimensión.

        Returns: {
            "series":       [{label: "2025-01", value: 12500.0}, ...],  ← total
            "by_dim":       {dim_value: [{label, value}]},              ← si dim_field
            "periods":      int,
            "metric_field": str,
            "date_field":   str,
            "last3_avg":    float,
            "prev3_avg":    float,
            "trend_pct":    float,
            "forecast":     [{label, value}],                           ← 3 períodos
        }
        """
        date_fld  = fields.get("date") or fields.get("metric")   # fallback
        metric_fld = fields.get("metric")
        if not metric_fld:
            return {"error": "No se encontró campo de métrica"}

        monthly    = defaultdict(float)
        by_dim     = defaultdict(lambda: defaultdict(float))

        for rec in records:
            period = _period_label(rec, date_fld)
            if period is None:
                continue
            val = _num(rec.get(metric_fld))
            monthly[period] += val

            if dim_field:
                dim_val = _str_val(rec.get(dim_field))
                by_dim[dim_val][period] += val

        labels = sorted(monthly)
        values = [monthly[lbl] for lbl in labels]
        last3  = values[-3:] if len(values) >= 3 else values
        prev3  = values[-6:-3] if len(values) >= 6 else values[:-3]
        avg_l3 = _avg(last3)
        avg_p3 = _avg(prev3) if prev3 else avg_l3
        trend  = _pct(avg_l3, avg_p3)
        mod_t  = max(min(trend, 20), -20) * 0.6
        proj   = avg_l3 * (1 + mod_t / 100)
        next_lbs = _next_month_labels(labels[-1] if labels else "2025-01", 3)

        result = {
            "series":       [{"label": lbl, "value": round(monthly[lbl], 2)} for lbl in labels],
            "by_dim":       {},
            "periods":      len(labels),
            "metric_field": metric_fld,
            "date_field":   date_fld,
            "last3_avg":    round(avg_l3, 2),
            "prev3_avg":    round(avg_p3, 2),
            "trend_pct":    round(trend, 1),
            "forecast":     [{"label": lbl, "value": round(proj, 2)} for lbl in next_lbs],
        }

        if dim_field and by_dim:
            result["by_dim"] = {
                dv: [{"label": lbl, "value": round(by_dim[dv].get(lbl, 0), 2)} for lbl in labels]
                for dv in sorted(by_dim)
            }

        return result

    # ----------------------------------------------------------------
    # Dimension ranking
    # ----------------------------------------------------------------

    def _run_dimension_ranking(self, fields, records, dim_field=None, combo=None, top_n=20):
        """
        Ranking y participación para una o dos dimensiones.

        Returns: {
            "items": [{
                "key":       str,
                "label":     str,
                "value":     float,
                "share_pct": float,
                "rank":      int,
                "clients":   int,
                "orders":    int,
            }],
            "total":          float,
            "dimension_field": str,
            "metric_field":   str,
            "concentration_top3_pct": float,
            "hhi":            float,   ← Herfindahl–Hirschman Index
        }
        """
        metric_fld = fields.get("metric")
        dim_a      = (combo.get("dim_a") if combo else None) or dim_field or _first_dim(fields)
        dim_b      = combo.get("dim_b") if combo else None

        if not metric_fld or not dim_a:
            return {"error": "Se requiere al menos una dimensión y una métrica"}

        grouped = defaultdict(lambda: {"value": 0.0, "clients": set(), "orders": set()})

        for rec in records:
            val_a = _str_val(rec.get(dim_a))
            key   = f"{val_a} / {_str_val(rec.get(dim_b))}" if dim_b else val_a
            v     = _num(rec.get(metric_fld))
            grouped[key]["value"]   += v
            grouped[key]["clients"].add(_str_val(rec.get("client_key") or rec.get("client") or ""))
            grouped[key]["orders"].add(_str_val(rec.get("invoice") or ""))

        total      = sum(g["value"] for g in grouped.values())
        ordered    = sorted(grouped.items(), key=lambda x: x[1]["value"], reverse=True)[:top_n]
        top3_share = sum(g["value"] for _, g in ordered[:3]) / max(total, 1) * 100
        shares     = [g["value"] / max(total, 1) for _, g in ordered]
        hhi        = round(sum(s ** 2 for s in shares) * 10000, 1)

        items = [
            {
                "key":       key,
                "label":     key,
                "value":     round(grp["value"], 2),
                "share_pct": round(grp["value"] / max(total, 1) * 100, 1),
                "rank":      rank + 1,
                "clients":   len(grp["clients"]),
                "orders":    len(grp["orders"]),
            }
            for rank, (key, grp) in enumerate(ordered)
        ]

        return {
            "items":                   items,
            "total":                   round(total, 2),
            "dimension_field":         dim_a,
            "metric_field":            metric_fld,
            "concentration_top3_pct":  round(top3_share, 1),
            "hhi":                     hhi,
        }

    # ----------------------------------------------------------------
    # Recurrencia y churn
    # ----------------------------------------------------------------

    def _run_recurrence_churn(self, fields, records, dim_field=None, combo=None):
        """
        Clasifica la cartera de clientes por recurrencia.

        Returns: {
            "by_status":      {"Activo": n, "Dormido": n, "Reactivable": n, "Perdido": n},
            "status_sales":   {"Activo": float, ...},
            "clients":        [ClientRow],   ← top 50 ordenados por riesgo
            "total_clients":  int,
            "active_pct":     float,
            "recurring_pct":  float,         ← compraron ≥4 meses últimos 12m
            "avg_ticket":     float,
            "churn_at_risk_sales": float,    ← venta histórica de dormidos+reactivables
        }
        """
        date_fld   = fields.get("date")
        client_fld = fields.get("client")
        metric_fld = fields.get("metric")
        invoice_fld = "invoice"

        if not client_fld or not metric_fld:
            return {"error": "Se requieren campos de cliente y métrica"}

        # Determinar fecha máxima
        dates = [rec.get(date_fld) for rec in records if isinstance(rec.get(date_fld), (date, datetime))]
        max_date = max(dates) if dates else date.today()
        if isinstance(max_date, datetime):
            max_date = max_date.date()
        cut12 = max_date - timedelta(days=364)

        # Agrupar por cliente
        grouped = defaultdict(list)
        for rec in records:
            ckey = _str_val(rec.get(client_fld))
            if ckey:
                grouped[ckey].append(rec)

        by_status     = defaultdict(int)
        status_sales  = defaultdict(float)
        client_rows   = []

        for ckey, items in grouped.items():
            rec_dates = [r.get(date_fld) for r in items if isinstance(r.get(date_fld), (date, datetime))]
            rec_dates = [d.date() if isinstance(d, datetime) else d for d in rec_dates]

            if not rec_dates:
                status = "Perdido"
                last_date = None
                recency   = 9999
                avg_gap   = 60
            else:
                last_date = max(rec_dates)
                recency   = (max_date - last_date).days
                sorted_d  = sorted(set(rec_dates))
                gaps      = [(b - a).days for a, b in zip(sorted_d, sorted_d[1:])]
                avg_gap   = median(gaps) if gaps else 45
                status    = _classify_client(recency, avg_gap)

            items12  = [r for r in items if isinstance(r.get(date_fld), (date, datetime)) and
                        (r[date_fld].date() if isinstance(r[date_fld], datetime) else r[date_fld]) >= cut12]
            ref      = items12 or items
            sales12  = round(sum(_num(r.get(metric_fld)) for r in items12), 2)
            sales_all = round(sum(_num(r.get(metric_fld)) for r in items), 2)
            orders   = len({_str_val(r.get(invoice_fld)) for r in ref})
            months_a = len({(r[date_fld].date() if isinstance(r[date_fld], datetime) else r[date_fld]).strftime("%Y-%m")
                            for r in items12 if isinstance(r.get(date_fld), (date, datetime))})

            by_status[status] += 1
            status_sales[status] += sales12

            seg_val = _str_val(records[0].get(dim_field, "")) if dim_field and records else None
            client_rows.append({
                "client_key":    ckey,
                "client":        _str_val(items[-1].get("client") or ckey),
                "status":        status,
                "sales12m":      sales12,
                "salesHistory":  sales_all,
                "lastDate":      last_date.isoformat() if last_date else None,
                "recencyDays":   recency,
                "avgGapDays":    round(avg_gap, 1),
                "monthsActive":  months_a,
                "avgTicket":     round(sales_all / max(orders, 1), 2),
                "orders":        orders,
                "segment":       seg_val,
            })

        total         = max(len(client_rows), 1)
        active        = by_status.get("Activo", 0)
        recurring     = sum(1 for c in client_rows if c["monthsActive"] >= 4)
        avg_ticket    = round(sum(c["avgTicket"] for c in client_rows) / total, 2)
        churn_at_risk = sum(c["salesHistory"] for c in client_rows if c["status"] in {"Dormido", "Reactivable"})

        # Ordenar: primero los de mayor riesgo y mayor venta histórica
        _status_rank = {"Perdido": 0, "Reactivable": 1, "Dormido": 2, "Activo": 3}
        client_rows.sort(key=lambda c: (_status_rank.get(c["status"], 9), -c["salesHistory"]))

        return {
            "by_status":           dict(by_status),
            "status_sales":        {s: round(v, 2) for s, v in status_sales.items()},
            "clients":             client_rows[:50],
            "total_clients":       total,
            "active_pct":          round(active / total * 100, 1),
            "recurring_pct":       round(recurring / total * 100, 1),
            "avg_ticket":          avg_ticket,
            "churn_at_risk_sales": round(churn_at_risk, 2),
            "client_field":        client_fld,
            "metric_field":        metric_fld,
        }

    # ----------------------------------------------------------------
    # Cross-sell / mix
    # ----------------------------------------------------------------

    def _run_cross_sell_mix(self, fields, records, dim_field=None, combo=None):
        """
        Amplitud de surtido por cliente (cuántas categorías compra cada uno).

        Returns: {
            "by_client": [{client, categories: [str], breadth: int, value: float}],
            "avg_breadth":           float,
            "low_breadth_clients":   int,    ← breadth == 1
            "breadth_distribution":  {1: n, 2: n, ...},
            "product_field":         str,
            "client_field":          str,
        }
        """
        client_fld  = fields.get("client")
        product_fld = fields.get("product")
        metric_fld  = fields.get("metric") or "amount"

        if not client_fld or not product_fld:
            return {"error": "Se requieren campos de cliente y producto/familia"}

        grouped = defaultdict(lambda: {"cats": set(), "value": 0.0})
        for rec in records:
            ckey = _str_val(rec.get(client_fld))
            prod = _str_val(rec.get(product_fld))
            if not ckey or not prod:
                continue
            grouped[ckey]["cats"].add(prod)
            grouped[ckey]["value"] += _num(rec.get(metric_fld))

        breadth_dist = defaultdict(int)
        rows = []
        for ckey, data in grouped.items():
            b = len(data["cats"])
            breadth_dist[b] += 1
            rows.append({
                "client":     ckey,
                "categories": sorted(data["cats"]),
                "breadth":    b,
                "value":      round(data["value"], 2),
            })

        rows.sort(key=lambda r: (-r["breadth"], -r["value"]))
        total    = max(len(rows), 1)
        avg_b    = round(sum(r["breadth"] for r in rows) / total, 2)
        low_b    = sum(1 for r in rows if r["breadth"] <= 1)

        return {
            "by_client":           rows[:50],
            "avg_breadth":         avg_b,
            "low_breadth_clients": low_b,
            "breadth_distribution": dict(sorted(breadth_dist.items())),
            "product_field":       product_fld,
            "client_field":        client_fld,
        }

    # ----------------------------------------------------------------
    # Seller performance
    # ----------------------------------------------------------------

    def _run_seller_performance(self, fields, records, dim_field=None, combo=None):
        """
        Performance de vendedores: venta, clientes únicos, participación, crecimiento.

        Returns: {
            "sellers": [{seller, value, share_pct, clients, orders, growth_pct}],
            "total":   float,
            "concentration_top3_pct": float,
            "seller_field": str,
            "metric_field": str,
        }
        """
        seller_fld = fields.get("seller")
        metric_fld = fields.get("metric")
        if not seller_fld or not metric_fld:
            return {"error": "Se requieren campos de vendedor y métrica"}

        # Determinar corte para crecimiento (90 vs 90 días previos)
        dates = [r.get("date") for r in records if isinstance(r.get("date"), (date, datetime))]
        dates = [d.date() if isinstance(d, datetime) else d for d in dates]
        max_d = max(dates) if dates else date.today()
        cur_start  = max_d - timedelta(days=89)
        prev_start = cur_start - timedelta(days=90)
        prev_end   = cur_start - timedelta(days=1)

        grouped = defaultdict(lambda: {"value": 0.0, "cur": 0.0, "prev": 0.0,
                                        "clients": set(), "orders": set()})
        for rec in records:
            sel = _str_val(rec.get(seller_fld))
            if not sel:
                continue
            v   = _num(rec.get(metric_fld))
            d   = rec.get("date")
            if isinstance(d, datetime):
                d = d.date()
            grouped[sel]["value"]   += v
            grouped[sel]["clients"].add(_str_val(rec.get("client_key") or rec.get("client") or ""))
            grouped[sel]["orders"].add(_str_val(rec.get("invoice") or ""))
            if d and d >= cur_start:
                grouped[sel]["cur"] += v
            if d and prev_start <= d <= prev_end:
                grouped[sel]["prev"] += v

        total   = sum(g["value"] for g in grouped.values())
        ordered = sorted(grouped.items(), key=lambda x: x[1]["value"], reverse=True)
        top3    = sum(g["value"] for _, g in ordered[:3]) / max(total, 1) * 100

        sellers = [
            {
                "seller":     sel,
                "value":      round(grp["value"], 2),
                "share_pct":  round(grp["value"] / max(total, 1) * 100, 1),
                "clients":    len(grp["clients"]),
                "orders":     len(grp["orders"]),
                "growth_pct": _pct(grp["cur"], grp["prev"]),
            }
            for sel, grp in ordered
        ]

        return {
            "sellers":                sellers,
            "total":                  round(total, 2),
            "concentration_top3_pct": round(top3, 1),
            "seller_field":           seller_fld,
            "metric_field":           metric_fld,
        }

    # ----------------------------------------------------------------
    # Geographic coverage
    # ----------------------------------------------------------------

    def _run_geographic_coverage(self, fields, records, dim_field=None, combo=None):
        """
        Venta y cobertura por ruta.

        Returns: {
            "routes": [{route, value, share_pct, clients, covered: bool}],
            "total":               float,
            "uncovered_value":     float,
            "uncovered_pct":       float,
            "route_field":         str,
        }
        """
        route_fld  = fields.get("route")
        metric_fld = fields.get("metric")
        if not route_fld or not metric_fld:
            return {"error": "Se requieren campos de ruta y métrica"}

        _missing_route = {"Sin ruta", "sin ruta", ""}
        grouped = defaultdict(lambda: {"value": 0.0, "clients": set()})
        uncovered = 0.0

        for rec in records:
            rt  = _str_val(rec.get(route_fld))
            v   = _num(rec.get(metric_fld))
            grouped[rt]["value"] += v
            grouped[rt]["clients"].add(_str_val(rec.get("client_key") or rec.get("client") or ""))
            if rt.lower() in _missing_route or rt in _MISSING:
                uncovered += v

        total   = sum(g["value"] for g in grouped.values())
        ordered = sorted(grouped.items(), key=lambda x: x[1]["value"], reverse=True)

        routes = [
            {
                "route":      rt,
                "value":      round(grp["value"], 2),
                "share_pct":  round(grp["value"] / max(total, 1) * 100, 1),
                "clients":    len(grp["clients"]),
                "covered":    rt not in _MISSING and rt.lower() not in _missing_route,
            }
            for rt, grp in ordered
        ]

        return {
            "routes":          routes,
            "total":           round(total, 2),
            "uncovered_value": round(uncovered, 2),
            "uncovered_pct":   round(uncovered / max(total, 1) * 100, 1),
            "route_field":     route_fld,
        }

    # ----------------------------------------------------------------
    # Margin analysis
    # ----------------------------------------------------------------

    def _run_margin_analysis(self, fields, records, dim_field=None, combo=None):
        """
        Análisis de margen bruto por dimensión.

        Returns: {
            "items": [{label, revenue, cost, margin, margin_pct, volume}],
            "total_revenue":  float,
            "total_cost":     float,
            "total_margin":   float,
            "total_margin_pct": float,
            "revenue_field":  str,
            "cost_field":     str,
        }
        """
        rev_fld  = fields.get("revenue")
        cost_fld = fields.get("cost")
        dim_a    = (combo.get("dim_a") if combo else None) or dim_field

        if not rev_fld or not cost_fld:
            return {"error": "Se requieren campos de venta y costo"}

        grouped = defaultdict(lambda: {"rev": 0.0, "cost": 0.0, "vol": 0})

        for rec in records:
            key = _str_val(rec.get(dim_a)) if dim_a else "Total"
            grouped[key]["rev"]  += _num(rec.get(rev_fld))
            grouped[key]["cost"] += _num(rec.get(cost_fld))
            grouped[key]["vol"]  += 1

        ordered = sorted(grouped.items(), key=lambda x: x[1]["rev"], reverse=True)
        items = [
            {
                "label":      key,
                "revenue":    round(g["rev"], 2),
                "cost":       round(g["cost"], 2),
                "margin":     round(g["rev"] - g["cost"], 2),
                "margin_pct": round((g["rev"] - g["cost"]) / max(g["rev"], 1) * 100, 1),
                "volume":     g["vol"],
            }
            for key, g in ordered
        ]

        t_rev  = sum(g["rev"]  for g in grouped.values())
        t_cost = sum(g["cost"] for g in grouped.values())

        return {
            "items":             items,
            "total_revenue":     round(t_rev, 2),
            "total_cost":        round(t_cost, 2),
            "total_margin":      round(t_rev - t_cost, 2),
            "total_margin_pct":  round((t_rev - t_cost) / max(t_rev, 1) * 100, 1),
            "revenue_field":     rev_fld,
            "cost_field":        cost_fld,
        }

    # ----------------------------------------------------------------
    # Sales force breakdown
    # ----------------------------------------------------------------

    def _run_sales_force_breakdown(self, fields, records, dim_field=None, combo=None):
        """
        Desglose por fuerza de ventas, con crecimiento trimestral.
        Reutiliza la lógica de seller_performance adaptada al campo de fuerza.

        Returns: igual que seller_performance pero con "force" en lugar de "seller"
        """
        force_fld  = fields.get("sales_force")
        metric_fld = fields.get("metric")
        if not force_fld or not metric_fld:
            return {"error": "Se requieren campos de fuerza de ventas y métrica"}

        # Inyectar temporalmente como seller para reusar el método
        adapted = dict(fields, seller=force_fld)
        result = self._run_seller_performance(adapted, records, dim_field=dim_field)
        if "sellers" in result:
            result["forces"] = result.pop("sellers")
            result["seller_field"] = result.pop("seller_field", force_fld)
            result["force_field"]  = force_fld
        return result

    # ----------------------------------------------------------------
    # Product analysis
    # ----------------------------------------------------------------

    def _run_product_analysis(self, fields, records, dim_field=None, combo=None):
        """
        Participación y momentum de categorías de producto.

        Returns: {
            "items": [{label, value, share_pct, growth_pct, clients, orders}],
            "total":          float,
            "top_category":   str,
            "product_field":  str,
            "metric_field":   str,
        }
        """
        prod_fld   = fields.get("product")
        metric_fld = fields.get("metric")
        if not prod_fld or not metric_fld:
            return {"error": "Se requieren campos de producto/familia y métrica"}

        # Corte temporal para crecimiento
        dates = [r.get("date") for r in records if isinstance(r.get("date"), (date, datetime))]
        dates = [d.date() if isinstance(d, datetime) else d for d in dates]
        max_d = max(dates) if dates else date.today()
        cur_start  = max_d - timedelta(days=89)
        prev_start = cur_start - timedelta(days=90)
        prev_end   = cur_start - timedelta(days=1)

        grouped = defaultdict(lambda: {"value": 0.0, "cur": 0.0, "prev": 0.0,
                                        "clients": set(), "orders": set()})

        for rec in records:
            cat = _str_val(rec.get(prod_fld))
            if not cat:
                continue
            v = _num(rec.get(metric_fld))
            d = rec.get("date")
            if isinstance(d, datetime):
                d = d.date()
            grouped[cat]["value"]   += v
            grouped[cat]["clients"].add(_str_val(rec.get("client_key") or rec.get("client") or ""))
            grouped[cat]["orders"].add(_str_val(rec.get("invoice") or ""))
            if d and d >= cur_start:
                grouped[cat]["cur"] += v
            if d and prev_start <= d <= prev_end:
                grouped[cat]["prev"] += v

        total   = sum(g["value"] for g in grouped.values())
        ordered = sorted(grouped.items(), key=lambda x: x[1]["value"], reverse=True)

        items = [
            {
                "label":      cat,
                "value":      round(g["value"], 2),
                "share_pct":  round(g["value"] / max(total, 1) * 100, 1),
                "growth_pct": _pct(g["cur"], g["prev"]),
                "clients":    len(g["clients"]),
                "orders":     len(g["orders"]),
            }
            for cat, g in ordered
        ]

        return {
            "items":         items,
            "total":         round(total, 2),
            "top_category":  items[0]["label"] if items else None,
            "product_field": prod_fld,
            "metric_field":  metric_fld,
        }

    # ----------------------------------------------------------------
    # Channel analysis
    # ----------------------------------------------------------------

    def _run_channel_analysis(self, fields, records, dim_field=None, combo=None):
        """
        Participación por canal de venta.

        Returns: {
            "items": [{label, value, share_pct, clients}],
            "total":         float,
            "channel_count": int,
            "top_channel":   str,
            "channel_field": str,
        }
        """
        channel_fld = fields.get("channel")
        metric_fld  = fields.get("metric")
        if not channel_fld or not metric_fld:
            return {"error": "Se requieren campos de canal y métrica"}

        grouped = defaultdict(lambda: {"value": 0.0, "clients": set()})
        for rec in records:
            ch  = _str_val(rec.get(channel_fld))
            if not ch:
                continue
            v   = _num(rec.get(metric_fld))
            grouped[ch]["value"]   += v
            grouped[ch]["clients"].add(_str_val(rec.get("client_key") or rec.get("client") or ""))

        total   = sum(g["value"] for g in grouped.values())
        ordered = sorted(grouped.items(), key=lambda x: x[1]["value"], reverse=True)

        items = [
            {
                "label":     ch,
                "value":     round(g["value"], 2),
                "share_pct": round(g["value"] / max(total, 1) * 100, 1),
                "clients":   len(g["clients"]),
            }
            for ch, g in ordered
        ]

        return {
            "items":         items,
            "total":         round(total, 2),
            "channel_count": len(items),
            "top_channel":   items[0]["label"] if items else None,
            "channel_field": channel_fld,
        }


# ---------------------------------------------------------------------------
# Helpers internos (sin estado, sin imports de otros módulos del proyecto)
# ---------------------------------------------------------------------------

def _num(value):
    """Convierte un valor a float, devuelve 0.0 si no es numérico."""
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _str_val(value):
    """Convierte a str limpio; filtra sentinelas."""
    if value is None or value is False:
        return ""
    s = str(value).strip()
    return s


def _period_label(rec, date_fld):
    """
    Devuelve el label de período para un registro.
    Prioridad: campo date → year+month → year → month
    """
    if date_fld:
        d = rec.get(date_fld)
        if isinstance(d, datetime):
            return d.strftime("%Y-%m")
        if isinstance(d, date):
            return d.strftime("%Y-%m")

    yr  = rec.get("year")
    mo  = rec.get("month")
    if yr and mo:
        return f"{int(yr):04d}-{int(mo):02d}"
    if yr:
        return str(int(yr))
    return None


def _avg(values):
    return sum(values) / len(values) if values else 0.0


def _pct(current, previous):
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round((current - previous) / previous * 100, 1)


def _classify_client(recency, avg_gap):
    if recency <= max(30, avg_gap * 1.2):
        return "Activo"
    if recency <= max(60, avg_gap * 2):
        return "Dormido"
    if recency <= max(120, avg_gap * 3):
        return "Reactivable"
    return "Perdido"


def _next_month_labels(last_label, periods):
    try:
        year, month = (int(p) for p in last_label.split("-"))
    except (ValueError, AttributeError):
        return []
    labels = []
    for _ in range(periods):
        month += 1
        if month > 12:
            month = 1
            year += 1
        labels.append(f"{year:04d}-{month:02d}")
    return labels


def _first_dim(fields):
    """Devuelve el primer campo que no sea métrica ni fecha, como dimensión por defecto."""
    skip = {"metric", "date", "revenue", "cost"}
    for slot, fld in fields.items():
        if slot not in skip and fld:
            return fld
    return None
