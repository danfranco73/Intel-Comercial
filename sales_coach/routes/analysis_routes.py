from __future__ import annotations

import gzip
import json
import logging
from contextlib import nullcontext
from time import perf_counter

from analyzer import merge_supplier_focus_filter
from bi.series_clickhouse import SeriesUnavailable
from clickhouse_client import build_clickhouse_series_report, clickhouse_configured, get_clickhouse_storage_status
from mongo_client import get_erp_storage_status, load_erp_articles_dataset
from sales_coach.cache import cached_status, data_version, result_cache, result_cache_key
from sales_coach.concurrency import AnalysisCapacityExceeded, heavy_analysis_slot
from sales_coach.repositories import AnalysisRepository, PreferencesRepository
from sales_coach.services import AnalysisService

# Una línea JSON por informe con tiempos y tamaños: es lo que se mira en los
# logs de Render para decidir la Etapa 3.
_PERF_LOGGER = logging.getLogger("codenoa.perf")
if not _PERF_LOGGER.handlers:
    _perf_handler = logging.StreamHandler()
    _perf_handler.setFormatter(logging.Formatter("%(message)s"))
    _PERF_LOGGER.addHandler(_perf_handler)
    _PERF_LOGGER.setLevel(logging.INFO)
    _PERF_LOGGER.propagate = False


def _current_data_version():
    try:
        return data_version(
            cached_status("mongo_storage", get_erp_storage_status),
            cached_status("clickhouse_storage", get_clickhouse_storage_status),
        )
    except Exception:
        # Sin estado de las bases el caché igual vence por TTL.
        return None


def _record_summary(result):
    return {
        "meta": result.get("meta", {}),
        "summary": result.get("summary", {}),
        "insightsSummary": result.get("insightsSummary"),
    }


def cached_report(kind, data, scope, compute, extra=None, heavy=True, user=None):
    """Devuelve (json gzip, extra, timings) de un informe, calculándolo una sola vez.

    Varios usuarios pidiendo lo mismo comparten el cálculo; el turno de análisis
    pesado solo se toma cuando de verdad hay que leer ventas. También lo usa el
    precálculo posterior a cada sync (services/report_warmer).
    """
    started = perf_counter()
    key = result_cache_key(kind, data, scope, _current_data_version())
    timings = {"cache": "hit"}
    entry = result_cache.get(key)
    if entry is None:
        with result_cache.single_flight(key):
            entry = result_cache.get(key)
            if entry is not None:
                timings["cache"] = "shared"
            else:
                timings["cache"] = "miss"
                wait_started = perf_counter()
                with heavy_analysis_slot() if heavy else nullcontext():
                    timings["wait_ms"] = (perf_counter() - wait_started) * 1000
                    compute_started = perf_counter()
                    result, service_timings = compute()
                    timings["load_ms"] = service_timings.get("load_ms", 0.0)
                    timings["compute_ms"] = (perf_counter() - compute_started) * 1000 - timings["load_ms"]
                serialize_started = perf_counter()
                body = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
                body_gzip = gzip.compress(body, compresslevel=5)
                timings["serialize_ms"] = (perf_counter() - serialize_started) * 1000
                timings["bytes"] = len(body)
                entry_extra = extra(result) if extra else {}
                del result, body
                result_cache.put(key, body_gzip, entry_extra)
                entry = {"body_gzip": body_gzip, "extra": entry_extra}
    timings["total_ms"] = (perf_counter() - started) * 1000
    timings["gzip_bytes"] = len(entry["body_gzip"])
    _log_report_timing(kind, timings, entry["extra"], user)
    return entry["body_gzip"], entry["extra"], timings


def _log_report_timing(kind, timings, extra, user):
    meta = (extra or {}).get("meta") or {}
    _PERF_LOGGER.info(
        json.dumps(
            {
                "event": "report",
                "kind": kind,
                "user": user,
                "rowsAnalyzed": meta.get("rowsAnalyzed"),
                "rowsUniverse": meta.get("rowsUniverse"),
                "cacheEntries": result_cache.stats()["entries"],
                **{name: round(value, 1) if isinstance(value, float) else value for name, value in timings.items()},
            },
            ensure_ascii=False,
        )
    )


def series_request_key(fecha_desde, fecha_hasta, filters=None, supplier_focus=None, compare=False):
    """Pedido normalizado de /api/bi/series: es la clave de su caché."""
    return {
        "fechaDesde": fecha_desde,
        "fechaHasta": fecha_hasta,
        "filters": merge_supplier_focus_filter(filters or {}, supplier_focus),
        "compare": bool(compare),
    }


def compute_series_report(request_key):
    mongo_status = cached_status("mongo_storage", get_erp_storage_status)
    report = build_clickhouse_series_report(
        request_key["fechaDesde"],
        request_key["fechaHasta"],
        request_key["filters"],
        compare=request_key["compare"],
        articles_version=mongo_status.get("articlesLastSyncAt"),
        load_articles=lambda: load_erp_articles_dataset()["records"],
    )
    return report, {}


class AnalysisRoutesMixin:
    def _analysis_service(self):
        return AnalysisService(self.resolve_datasets)

    def _cached_report(self, kind, data, compute, extra=None, heavy=True):
        user_id = getattr(getattr(self.auth_context, "user", None), "id", None)
        return cached_report(kind, data, self.data_scope, compute, extra=extra, heavy=heavy, user=user_id)

    @staticmethod
    def _server_timing(timings):
        parts = [f'cache;desc="{timings["cache"]}"']
        for name in ("wait", "load", "compute", "serialize", "total"):
            value = timings.get(f"{name}_ms")
            if value is not None:
                parts.append(f"{name};dur={value:.1f}")
        return {"Server-Timing": ", ".join(parts)}

    def handle_analyze(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        if not isinstance(data.get("datasets"), dict) or not data["datasets"]:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return

        def compute():
            service = self._analysis_service()
            return service.analyze(data, self.data_scope), service.timings

        try:
            body_gzip, summary, timings = self._cached_report("analyze", data, compute, extra=_record_summary)
        except AnalysisCapacityExceeded as exc:
            self.send_json({"error": str(exc)}, status=503)
            return
        except ValueError as exc:
            self.log_application_error(
                "analyze_validation",
                exc,
                {
                    "filters": data.get("filters", {}),
                    "datasets": list((data.get("datasets") or {}).keys()),
                },
            )
            self.send_json({"error": str(exc)}, status=422)
            return
        except Exception as exc:
            self.log_application_error(
                "analyze",
                exc,
                {
                    "filters": data.get("filters", {}),
                    "datasets": list((data.get("datasets") or {}).keys()),
                },
            )
            self.send_json({"error": str(exc)}, status=500)
            return

        db = self.database()
        if db is not None:
            try:
                AnalysisRepository(db).record(data.get("filters", {}), summary)
            except Exception as exc:
                self.log_application_error("analysis_record", exc)
        PreferencesRepository(db).save(
            self.auth_context.user.id,
            data.get("datasets", {}),
            planning=data.get("planning"),
        )
        self.send_precompressed_json(body_gzip, headers=self._server_timing(timings))

    def handle_bi_series(self):
        """Series de Comercial totalizadas en ClickHouse, sin traer filas a Python.

        Responde 409 con fallback "legacy" cuando el pedido necesita el informe
        completo: usuarios con alcance restringido (su cartera se resuelve en
        Python), filtros de vendedor/fuerza/ruta/esquema o sin ClickHouse.
        """
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        fecha_desde = str(data.get("fechaDesde") or "")
        fecha_hasta = str(data.get("fechaHasta") or "")
        if not fecha_desde or not fecha_hasta:
            self.send_json({"error": "Elegí fecha desde y fecha hasta"}, status=400)
            return
        if any(values for values in (self.data_scope or {}).values()) or not clickhouse_configured():
            self.send_json({"error": "Este informe se calcula completo", "fallback": "legacy"}, status=409)
            return
        request_key = series_request_key(
            fecha_desde, fecha_hasta, data.get("filters"), data.get("supplierFocus"), data.get("compare")
        )
        filters = request_key["filters"]
        compute = lambda: compute_series_report(request_key)
        try:
            body_gzip, _, timings = self._cached_report("series", request_key, compute, extra=_record_summary, heavy=False)
        except SeriesUnavailable as exc:
            self.send_json({"error": str(exc), "fallback": "legacy"}, status=409)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=422)
            return
        except Exception as exc:
            self.log_application_error("bi_series", exc, {"filters": filters})
            self.send_json({"error": str(exc)}, status=500)
            return
        self.send_precompressed_json(body_gzip, headers=self._server_timing(timings))

    def handle_analyze_dynamic(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        if not str(data.get("task_id") or "").strip():
            self.send_json({"error": "Falta task_id"}, status=400)
            return
        if not isinstance(data.get("datasets"), dict) or not data["datasets"]:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return
        try:
            self.send_json(self._analysis_service().dynamic(data, self.data_scope))
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=422)
        except Exception as exc:
            self.log_application_error("analyze_dynamic", exc)
            self.send_json({"error": str(exc)}, status=500)

    def handle_bi_consistency(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        datasets = data.get("datasets", {})
        if not isinstance(datasets, dict) or not datasets:
            self.send_json({"error": "No llegaron datasets para validar"}, status=400)
            return
        try:
            result = self._analysis_service().consistency(datasets, self.data_scope)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=422)
            return
        except Exception as exc:
            self.log_application_error("bi_consistency", exc)
            self.send_json({"error": str(exc)}, status=500)
            return
        self.send_json(result)

    def handle_bi_tactical_month(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        if not isinstance(data.get("datasets"), dict) or not data["datasets"]:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return
        def compute():
            service = self._analysis_service()
            return service.tactical(data, self.data_scope), service.timings

        try:
            body_gzip, _, timings = self._cached_report("tactical_month", data, compute)
        except AnalysisCapacityExceeded as exc:
            self.send_json({"error": str(exc)}, status=503)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=422)
            return
        except Exception as exc:
            self.log_application_error("bi_tactical_month", exc)
            self.send_json({"error": str(exc)}, status=500)
            return
        self.send_precompressed_json(body_gzip, headers=self._server_timing(timings))

    def handle_get_session(self):
        session = PreferencesRepository(self.database()).load(self.auth_context.user.id)
        self.send_json(session if session else {"datasets": None})

    def handle_save_session(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        saved = PreferencesRepository(self.database()).save(
            self.auth_context.user.id,
            data.get("datasets", {}),
            planning=data.get("planning"),
        )
        self.send_json({"saved": saved})

    def handle_list_analyses(self, parsed):
        db = self.database()
        if db is None:
            self.send_json({"error": "MongoDB no configurado"}, status=503)
            return
        from urllib.parse import parse_qs

        query = parse_qs(parsed.query)
        try:
            limit = int(query.get("limit", [50])[0])
        except (TypeError, ValueError):
            limit = 50
        self.send_json({"analyses": AnalysisRepository(db).list_recent(limit)})
