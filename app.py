import cgi
import json
import os
import traceback
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlparse

from analyzer import DATASET_DEFINITIONS, analyze_datasets, suggest_mappings
from schema_detector import detect_schema_from_raw
from rule_engine import resolve_tasks, tasks_by_domain
from analysis_engine import AnalysisEngine
from kpi_generator import generate_kpis
from viz_selector import build_all_viz, build_viz
from insight_writer import write_insights, insights_summary
from clickhouse_client import (
    get_clickhouse_storage_status,
    load_erp_sales_dataset_clickhouse,
    sync_erp_sales_clickhouse,
)
from mongo_client import (
    get_db,
    get_erp_prefilter_options,
    get_erp_storage_status,
    get_mongo_sales_retention_cutoff,
    load_erp_articles_dataset,
    load_erp_routes_dataset,
    load_erp_sales_dataset,
    load_erp_sellers_dataset,
    ping,
    record_erp_sync_summary,
    save_session,
    load_session,
    sync_erp_articles,
    sync_erp_marketing,
    sync_erp_routes,
    sync_erp_sales,
    sync_erp_sellers,
)
from erp_client import (
    fetch_articles_dataset,
    fetch_marketing_dataset,
    fetch_routes_dataset,
    fetch_sales_dataset,
    fetch_staff_dataset,
    get_erp_status,
    erp_login,
)
from erp_master_builder import build_dataset, derive_routes_records, derive_sellers_records
from xlsx_reader import preview_sheet, read_sheet_names


BASE_DIR = Path(__file__).resolve().parent
PARENT_DIR = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
VENDOR_DIR = STATIC_DIR / "vendor"
UPLOAD_DIR = BASE_DIR / "uploads"
LOG_DIR = BASE_DIR / "logs"
ERROR_LOG = LOG_DIR / "app_errors.log"
HOST = "127.0.0.1"
PORT = 8765
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}
ALLOWED_ROOTS = (BASE_DIR, PARENT_DIR)
MAX_SCAN_DEPTH = 2
DEFAULT_ERP_SYNC_CHUNK_DAYS = 31
DEFAULT_MAX_JSON_BODY_BYTES = 512 * 1024
DEFAULT_MAX_UPLOAD_BODY_BYTES = 25 * 1024 * 1024
DEFAULT_ADMIN_RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_ADMIN_RATE_LIMIT_MAX_REQUESTS = 20
_RATE_LIMIT_LOCK = Lock()
_RATE_LIMIT_BUCKETS = defaultdict(deque)


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _erp_sales_chunk_days():
    try:
        value = int(os.getenv("CHESS_ERP_SALES_CHUNK_DAYS") or DEFAULT_ERP_SYNC_CHUNK_DAYS)
    except (TypeError, ValueError):
        value = DEFAULT_ERP_SYNC_CHUNK_DAYS
    return max(1, value)


def _parse_iso_date(value, field_name):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} debe tener formato YYYY-MM-DD")


def _build_sales_analysis_window(fecha_desde, fecha_hasta):
    start = _parse_iso_date(fecha_desde, "fechaDesde")
    end = _parse_iso_date(fecha_hasta, "fechaHasta")
    if start > end:
        raise ValueError("fechaDesde no puede ser mayor que fechaHasta")
    days = (end - start).days + 1
    comparison_end = start - timedelta(days=1)
    comparison_start = comparison_end - timedelta(days=days - 1)
    return {
        "selectedStart": start.isoformat(),
        "selectedEnd": end.isoformat(),
        "comparisonStart": comparison_start.isoformat(),
        "comparisonEnd": comparison_end.isoformat(),
        "loadStart": comparison_start.isoformat(),
        "loadEnd": end.isoformat(),
        "days": days,
    }


def _attach_sales_analysis_window(dataset, window):
    attached = dict(dataset)
    attached["analysisRange"] = {
        "fechaDesde": window["selectedStart"],
        "fechaHasta": window["selectedEnd"],
    }
    attached["comparisonRange"] = {
        "fechaDesde": window["comparisonStart"],
        "fechaHasta": window["comparisonEnd"],
    }
    attached["loadRange"] = {
        "fechaDesde": window["loadStart"],
        "fechaHasta": window["loadEnd"],
    }
    attached["comparisonDays"] = window["days"]
    return attached


def _parse_int_env(name, default_value, minimum=None):
    try:
        value = int(os.getenv(name) or default_value)
    except (TypeError, ValueError):
        value = default_value
    if minimum is not None:
        value = max(minimum, value)
    return value


def _max_json_body_bytes():
    return _parse_int_env("APP_MAX_JSON_BODY_BYTES", DEFAULT_MAX_JSON_BODY_BYTES, minimum=1024)


def _max_upload_body_bytes():
    return _parse_int_env("APP_MAX_UPLOAD_BODY_BYTES", DEFAULT_MAX_UPLOAD_BODY_BYTES, minimum=1024 * 1024)


def _admin_rate_limit_window_seconds():
    return _parse_int_env(
        "APP_ADMIN_RATE_LIMIT_WINDOW_SECONDS",
        DEFAULT_ADMIN_RATE_LIMIT_WINDOW_SECONDS,
        minimum=1,
    )


def _admin_rate_limit_max_requests():
    return _parse_int_env(
        "APP_ADMIN_RATE_LIMIT_MAX_REQUESTS",
        DEFAULT_ADMIN_RATE_LIMIT_MAX_REQUESTS,
        minimum=1,
    )


def _admin_token():
    return (os.getenv("APP_ADMIN_TOKEN") or os.getenv("ADMIN_TOKEN") or "").strip()


def _admin_auth_enabled():
    return bool(_admin_token())


def _client_ip(handler):
    forwarded = (handler.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return handler.client_address[0]


def _check_rate_limit(client_ip, scope):
    window = _admin_rate_limit_window_seconds()
    limit = _admin_rate_limit_max_requests()
    now = datetime.now(timezone.utc).timestamp()
    bucket_key = f"{client_ip}:{scope}"
    with _RATE_LIMIT_LOCK:
        bucket = _RATE_LIMIT_BUCKETS[bucket_key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
    return True


def _iter_date_chunks(fecha_desde, fecha_hasta, chunk_days=None):
    start = _parse_iso_date(fecha_desde, "fechaDesde")
    end = _parse_iso_date(fecha_hasta, "fechaHasta")
    if start > end:
        raise ValueError("fechaDesde no puede ser mayor que fechaHasta")
    window = max(1, chunk_days or _erp_sales_chunk_days())
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=window - 1), end)
        yield cursor.isoformat(), chunk_end.isoformat()
        cursor = chunk_end + timedelta(days=1)


def _fetch_sales_dataset_chunked(fecha_desde, fecha_hasta, detailed=True, cookie=None):
    records = []
    headers = []
    rows_read = 0
    warnings = []
    chunks = []
    for index, (chunk_start, chunk_end) in enumerate(_iter_date_chunks(fecha_desde, fecha_hasta), start=1):
        dataset = fetch_sales_dataset(chunk_start, chunk_end, detailed=detailed, cookie=cookie)
        if dataset.get("headers") and not headers:
            headers = dataset["headers"]
        rows_read += dataset.get("rowsRead", 0)
        records.extend(dataset.get("records", []))
        chunk_warning = dataset.get("warning")
        chunk_summary = {
            "index": index,
            "fechaDesde": chunk_start,
            "fechaHasta": chunk_end,
            "rowsRead": dataset.get("rowsRead", 0),
            "rowsValid": dataset.get("rowsValid", 0),
        }
        if chunk_warning:
            chunk_summary["warning"] = chunk_warning
            warnings.append(f"{chunk_start} a {chunk_end}: {chunk_warning}")
        chunks.append(chunk_summary)

    detail_label = "detalladas" if detailed else "resumen"
    source_label = f"ChessERP ventas {detail_label} {fecha_desde} a {fecha_hasta}"
    warning = None
    if not records and warnings:
        warning = " | ".join(warnings)
    return {
        "datasetType": "sales",
        "sourceKind": "erp",
        "file": source_label,
        "sheet": "API ventas detalladas" if detailed else "API ventas",
        "headerRow": 0,
        "rowsRead": rows_read,
        "rowsValid": len(records),
        "headers": headers,
        "mapping": {},
        "records": records,
        "warning": warning,
        "warnings": warnings,
        "chunkCount": len(chunks),
        "chunks": chunks,
        "sourceCount": 1,
        "sources": [
            {
                "file": source_label,
                "sheet": "API ventas detalladas" if detailed else "API ventas",
                "headerRow": 0,
                "rowsRead": rows_read,
                "rowsValid": len(records),
                "sourceKind": "erp",
            }
        ],
    }


def _mongo_sync_overlap(fecha_desde: str, fecha_hasta: str):
    cutoff = get_mongo_sales_retention_cutoff()
    if not cutoff:
        return fecha_desde, fecha_hasta
    start = _parse_iso_date(fecha_desde, "fechaDesde")
    end = _parse_iso_date(fecha_hasta, "fechaHasta")
    cutoff_date = date.fromisoformat(cutoff)
    if end < cutoff_date:
        return None
    overlap_start = max(start, cutoff_date)
    return overlap_start.isoformat(), end.isoformat()


def _sync_sales_range_chunked(fecha_desde, fecha_hasta, cookie=None):
    chunk_summaries = []
    warnings = []
    total_rows_read = 0
    total_rows_valid = 0
    total_deleted = 0
    total_upserted = 0
    total_modified = 0
    total_matched = 0
    total_row_versions = 0
    total_clickhouse_stored = 0
    total_mongo_stored = 0
    mongo_chunk_count = 0

    for index, (chunk_start, chunk_end) in enumerate(_iter_date_chunks(fecha_desde, fecha_hasta), start=1):
        dataset = fetch_sales_dataset(chunk_start, chunk_end, detailed=True, cookie=cookie)
        mongo_overlap = _mongo_sync_overlap(chunk_start, chunk_end)
        if mongo_overlap:
            mongo_start, mongo_end = mongo_overlap
            mongo_records = [
                record for record in dataset["records"]
                if record.get("date") and mongo_start <= record.get("date").isoformat() <= mongo_end
            ]
            sync_summary = sync_erp_sales(
                mongo_records,
                mongo_start,
                mongo_end,
                origin="api_sync_chunk",
                rows_read=dataset.get("rowsRead", 0),
                warning=dataset.get("warning"),
                empty_confirmed=not mongo_records,
            )
            mongo_chunk_count += 1
            total_mongo_stored += sync_summary.get("recordsStored", 0)
        else:
            sync_summary = {
                "range": {"fechaDesde": chunk_start, "fechaHasta": chunk_end},
                "deleted": 0,
                "upserted": 0,
                "modified": 0,
                "matched": 0,
                "recordsStored": 0,
                "recordsReceived": 0,
                "storageMode": "clickhouse_only",
            }
        clickhouse_summary = sync_erp_sales_clickhouse(
            dataset["records"],
            chunk_start,
            chunk_end,
            origin="api_sync_chunk",
            rows_read=dataset.get("rowsRead", 0),
            warning=dataset.get("warning"),
        )
        chunk_summary = {
            "index": index,
            "range": {"fechaDesde": chunk_start, "fechaHasta": chunk_end},
            "rowsRead": dataset.get("rowsRead", 0),
            "rowsValid": dataset.get("rowsValid", 0),
            "deleted": sync_summary.get("deleted", 0),
            "upserted": sync_summary.get("upserted", 0),
            "modified": sync_summary.get("modified", 0),
            "matched": sync_summary.get("matched", 0),
            "mongoStored": sync_summary.get("recordsStored", 0),
            "clickhouseStored": clickhouse_summary.get("recordsStored", 0) if clickhouse_summary.get("configured") else 0,
            "storageMode": "mongo_and_clickhouse" if mongo_overlap else "clickhouse_only",
        }
        if dataset.get("warning"):
            chunk_summary["warning"] = dataset["warning"]
            warnings.append(f"{chunk_start} a {chunk_end}: {dataset['warning']}")
        chunk_summaries.append(chunk_summary)
        total_rows_read += dataset.get("rowsRead", 0)
        total_rows_valid += dataset.get("rowsValid", 0)
        total_deleted += sync_summary.get("deleted", 0)
        total_upserted += sync_summary.get("upserted", 0)
        total_modified += sync_summary.get("modified", 0)
        total_matched += sync_summary.get("matched", 0)
        total_row_versions += sync_summary.get("rowVersions", 0)
        total_clickhouse_stored += clickhouse_summary.get("recordsStored", 0) if clickhouse_summary.get("configured") else 0

    summary = {
        "range": {"fechaDesde": fecha_desde, "fechaHasta": fecha_hasta},
        "rowsRead": total_rows_read,
        "rowsValid": total_rows_valid,
        "recordsReceived": total_rows_valid,
        "rowVersions": total_row_versions,
        "deleted": total_deleted,
        "upserted": total_upserted,
        "modified": total_modified,
        "matched": total_matched,
        "mongoStored": total_mongo_stored,
        "clickhouseStored": total_clickhouse_stored,
        "mongoChunkCount": mongo_chunk_count,
        "chunkCount": len(chunk_summaries),
        "chunkDays": _erp_sales_chunk_days(),
        "warning": " | ".join(warnings) if warnings and not total_rows_valid else None,
        "warnings": warnings,
        "chunks": chunk_summaries,
        "origin": "api_sync_batch",
    }
    return record_erp_sync_summary("sales_batch", summary)


class AppHandler(BaseHTTPRequestHandler):
    def _send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'",
        )

    def _send_json_error(self, message, status):
        payload = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            raise ValueError("Content-Length inválido")
        if length < 0:
            raise ValueError("Content-Length inválido")
        if length > _max_json_body_bytes():
            raise ValueError("Payload demasiado grande")
        payload = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            return json.loads(payload or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("JSON inválido") from exc

    def _ensure_admin_access(self, scope="admin"):
        client_ip = _client_ip(self)
        if not _check_rate_limit(client_ip, scope):
            self._send_json_error("Demasiadas solicitudes. Reintentá en unos segundos.", 429)
            return False
        if not _admin_auth_enabled():
            return True
        provided = (
            self.headers.get("X-Admin-Token")
            or self.headers.get("X-App-Admin-Token")
            or self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        )
        if provided and provided == _admin_token():
            return True
        self._send_json_error("Autenticación requerida para la superficie admin.", 401)
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self._send_security_headers()
            self.end_headers()
            return
        if parsed.path in {"/", "/bi"}:
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/admin":
            self.serve_file(STATIC_DIR / "admin.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.css":
            self.serve_file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self.serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path.startswith("/vendor/"):
            self.serve_vendor_file(parsed.path)
            return
        if parsed.path == "/api/session":
            self.handle_get_session()
            return
        if parsed.path == "/api/files":
            query = parse_qs(parsed.query)
            scope = query.get("scope", ["uploads"])[0]
            if scope != "uploads" and not self._ensure_admin_access(scope="files"):
                return
            self.send_json({"files": list_available_files(scope=scope)})
            return
        if parsed.path == "/api/datasets":
            self.send_json({"datasets": build_dataset_schema()})
            return
        if parsed.path == "/api/schema":
            self.handle_schema(parsed)
            return
        if parsed.path == "/api/possible-analyses":
            self.handle_possible_analyses(parsed)
            return
        if parsed.path == "/api/analyses":
            if not self._ensure_admin_access(scope="analyses"):
                return
            self.handle_list_analyses(parsed)
            return
        if parsed.path == "/api/workbook":
            self.handle_workbook(parsed)
            return
        if parsed.path == "/api/preview":
            self.handle_preview(parsed)
            return
        if parsed.path == "/api/erp/status":
            self.handle_erp_status()
            return
        if parsed.path == "/api/erp/storage-status":
            self.handle_erp_storage_status()
            return
        if parsed.path == "/api/clickhouse/storage-status":
            self.handle_clickhouse_storage_status()
            return
        if parsed.path == "/api/erp/prefilter-options":
            self.handle_erp_prefilter_options()
            return
        if parsed.path == "/api/admin/errors":
            if not self._ensure_admin_access(scope="admin_errors"):
                return
            self.handle_admin_errors(parsed)
            return
        self.send_error(404, "Ruta no encontrada")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/upload":
            if not self._ensure_admin_access(scope="upload"):
                return
            self.handle_upload()
            return
        if parsed.path == "/api/clear-uploads":
            if not self._ensure_admin_access(scope="clear_uploads"):
                return
            self.handle_clear_uploads()
            return
        if parsed.path == "/api/analyze":
            self.handle_analyze()
            return
        if parsed.path == "/api/analyze-dynamic":
            self.handle_analyze_dynamic()
            return
        if parsed.path == "/api/session":
            self.handle_save_session()
            return
        if parsed.path == "/api/db-status":
            self.send_json({"connected": ping()})
            return
        if parsed.path == "/api/erp/sync":
            if not self._ensure_admin_access(scope="erp_sync"):
                return
            self.handle_erp_sync()
            return
        self.send_error(404, "Ruta no encontrada")

    def handle_possible_analyses(self, parsed):
        """
        GET /api/possible-analyses?file=<nombre>&sheet=<hoja>&headerRow=<n>
        Devuelve los AnalysisTasks disponibles agrupados por dominio,
        listos para que el frontend construya el selector dinámico.
        """
        from xlsx_reader import preview_sheet
        query = parse_qs(parsed.query)
        filename = query.get("file", [None])[0]
        sheet_name = query.get("sheet", [None])[0]
        if not filename or not sheet_name:
            self.send_json({"error": "Faltan parámetros file y sheet"}, status=400)
            return
        target = safe_file_path(filename)
        if not target.exists():
            self.send_json({"error": f"No se encontró {filename}"}, status=404)
            return
        try:
            preview = preview_sheet(target, sheet_name, preview_rows=200)
            header_row = int(query.get("headerRow", [preview.get("headerRow", 0)])[0])
            headers = preview["headers"]
            rows = preview.get("rows", [])
            sample = rows[header_row + 1:] if header_row + 1 < len(rows) else rows
            schema = detect_schema_from_raw(headers, sample)
            tasks = resolve_tasks(schema)
            self.send_json({
                "file":          filename,
                "sheet":         sheet_name,
                "schema":        schema,
                "tasks":         tasks,
                "tasks_by_domain": tasks_by_domain(tasks),
            })
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_schema(self, parsed):
        """
        GET /api/schema?file=<nombre>&sheet=<hoja>&headerRow=<n>
        Devuelve el SchemaProfile detectado para un archivo sin necesidad de
        hacer un análisis completo (usa detect_schema_from_raw con muestra).
        """
        from xlsx_reader import preview_sheet
        query = parse_qs(parsed.query)
        filename = query.get("file", [None])[0]
        sheet_name = query.get("sheet", [None])[0]
        if not filename or not sheet_name:
            self.send_json({"error": "Faltan parámetros file y sheet"}, status=400)
            return
        target = safe_file_path(filename)
        if not target.exists():
            self.send_json({"error": f"No se encontró {filename}"}, status=404)
            return
        try:
            preview = preview_sheet(target, sheet_name, preview_rows=200)
            header_row = int(query.get("headerRow", [preview.get("headerRow", 0)])[0])
            headers = preview["headers"]
            rows = preview.get("rows", [])
            sample = rows[header_row + 1:] if header_row + 1 < len(rows) else rows
            schema = detect_schema_from_raw(headers, sample)
            self.send_json({"file": filename, "sheet": sheet_name, "schema": schema})
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_workbook(self, parsed):
        query = parse_qs(parsed.query)
        filename = query.get("file", [None])[0]
        if not filename:
            self.send_json({"error": "Falta el parámetro file"}, status=400)
            return

        target = safe_file_path(filename)
        if not target.exists():
            self.send_json({"error": f"No se encontró {filename}"}, status=404)
            return

        sheet_names = read_sheet_names(target)
        self.send_json(
            {
                "file": filename,
                "sheets": [{"name": name} for name in sheet_names],
                "defaultSheet": sheet_names[0] if sheet_names else None,
            }
        )

    def handle_preview(self, parsed):
        query = parse_qs(parsed.query)
        filename = query.get("file", [None])[0]
        sheet_name = query.get("sheet", [None])[0]
        if not filename or not sheet_name:
            self.send_json({"error": "Faltan file o sheet"}, status=400)
            return

        target = safe_file_path(filename)
        if not target.exists():
            self.send_json({"error": f"No se encontró {filename}"}, status=404)
            return

        preview = preview_sheet(target, sheet_name)
        dataset_type = query.get("datasetType", ["sales"])[0]
        self.send_json(
            {
                "file": filename,
                "sheet": sheet_name,
                "preview": preview,
                "mappingSuggestions": suggest_mappings(preview["headers"], dataset_type),
            }
        )

    def handle_erp_status(self):
        self.send_json(get_erp_status())

    def handle_erp_storage_status(self):
        self.send_json(get_erp_storage_status())

    def handle_clickhouse_storage_status(self):
        self.send_json(get_clickhouse_storage_status())

    def handle_erp_prefilter_options(self):
        self.send_json({"filters": get_erp_prefilter_options()})

    def handle_admin_errors(self, parsed):
        query = parse_qs(parsed.query)
        try:
            limit = min(max(int(query.get("limit", ["20"])[0]), 1), 100)
        except (TypeError, ValueError):
            limit = 20
        self.send_json({"errors": _read_recent_errors(limit=limit)})

    def handle_erp_sync(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        fecha_desde = data.get("fechaDesde")
        fecha_hasta = data.get("fechaHasta")
        refresh_masters = bool(data.get("refreshMasters"))
        if not fecha_desde or not fecha_hasta:
            self.send_json({"error": "Faltan fechaDesde o fechaHasta"}, status=400)
            return
        try:
            erp_session = erp_login()
            erp_cookie = erp_session.get("cookie")
            sync_summary = _sync_sales_range_chunked(fecha_desde, fecha_hasta, cookie=erp_cookie)
            storage = get_erp_storage_status()
            should_sync_masters = refresh_masters or not _erp_masters_available(storage)
            articles = sellers_dataset = routes_dataset = marketing_dataset = None
            article_summary = seller_summary = route_summary = marketing_summary = None
            if should_sync_masters:
                articles = fetch_articles_dataset(cookie=erp_cookie)
                sellers_dataset = fetch_staff_dataset(cookie=erp_cookie)
                routes_dataset = fetch_routes_dataset(cookie=erp_cookie)
                marketing_dataset = fetch_marketing_dataset(cookie=erp_cookie)
                article_summary = sync_erp_articles(articles["records"], origin="api_sync")
                seller_summary = sync_erp_sellers(sellers_dataset["records"], origin="api_sync")
                route_summary = sync_erp_routes(routes_dataset["records"], origin="api_sync")
                marketing_summary = sync_erp_marketing(marketing_dataset["records"], origin="api_sync")
                storage = get_erp_storage_status()
            self.send_json(
                {
                    "sync": sync_summary,
                    "articlesSync": article_summary,
                    "sellersSync": seller_summary,
                    "routesSync": route_summary,
                    "marketingSync": marketing_summary,
                    "storage": storage,
                    "clickhouseStorage": get_clickhouse_storage_status(),
                    "rowsRead": sync_summary.get("rowsRead", 0),
                    "rowsValid": sync_summary.get("rowsValid", 0),
                    "mongoStored": sync_summary.get("mongoStored", 0),
                    "clickhouseStored": sync_summary.get("clickhouseStored", 0),
                    "warning": sync_summary.get("warning"),
                    "warnings": sync_summary.get("warnings") or [],
                    "mastersSynced": should_sync_masters,
                    "articleRowsRead": articles["rowsRead"] if articles else 0,
                    "articleRowsValid": articles["rowsValid"] if articles else 0,
                    "sellerRowsValid": sellers_dataset["rowsValid"] if sellers_dataset else 0,
                    "routeRowsValid": routes_dataset["rowsValid"] if routes_dataset else 0,
                    "marketingRowsValid": marketing_dataset["rowsValid"] if marketing_dataset else 0,
                }
            )
        except Exception as exc:
            _log_error(
                "erp_sync",
                exc,
                {
                    "fechaDesde": fecha_desde,
                    "fechaHasta": fecha_hasta,
                    "refreshMasters": refresh_masters,
                },
            )
            self.send_json({"error": str(exc)}, status=500)

    def handle_upload(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            self.send_json({"error": "Content-Length inválido"}, status=400)
            return
        if content_length > _max_upload_body_bytes():
            self.send_json({"error": "Upload demasiado grande"}, status=413)
            return
        ctype, _ = cgi.parse_header(self.headers.get("content-type", ""))
        if ctype != "multipart/form-data":
            self.send_json({"error": "El upload debe enviarse como multipart/form-data"}, status=400)
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )

        items = form["files"] if "files" in form else []
        if not isinstance(items, list):
            items = [items]
        if not items:
            self.send_json({"error": "No llegaron archivos"}, status=400)
            return
        if len(items) > 4:
            self.send_json({"error": "Podés subir hasta 4 archivos por vez"}, status=400)
            return

        saved = []
        try:
            for item in items:
                if not getattr(item, "filename", ""):
                    continue
                saved.append(save_uploaded_file(item))
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)
            return

        self.send_json({"uploaded": saved, "files": list_available_files(scope="uploads")})

    def handle_clear_uploads(self):
        UPLOAD_DIR.mkdir(exist_ok=True)
        removed = 0
        for item in UPLOAD_DIR.iterdir():
            if item.is_file() and item.suffix.lower() in ALLOWED_EXTENSIONS:
                item.unlink()
                removed += 1
        self.send_json({"removed": removed, "files": list_available_files(scope="uploads")})

    def handle_analyze_dynamic(self):
        """
        POST /api/analyze-dynamic
        Body: {
            "datasets":  { ... },   ← mismo formato que /api/analyze
            "filters":   { ... },
            "task_id":   "temporal_trend",
            "combo":     {"dim_a": "seller_name", "dim_b": null}
        }
        Ejecuta un único AnalysisTask con el combo elegido por el usuario.
        """
        from analyzer import analyze_datasets
        from schema_detector import detect_schema
        from rule_engine import resolve_tasks

        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return

        task_id = data.get("task_id")
        combo   = data.get("combo")
        if not task_id:
            self.send_json({"error": "Falta task_id"}, status=400)
            return

        datasets = data.get("datasets", {})
        if not datasets:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return

        try:
            resolved = _resolve_datasets(datasets)
            # Importar solo lo necesario para cargar los registros enriquecidos
            from analyzer import (
                load_sales_dataset, load_dataset,
                enrich_sales, apply_filters,
                DATASET_DEFINITIONS,
            )
            loaded = {"sales": load_sales_dataset(resolved["sales"])} if "sales" in resolved else {}
            for dt, src in resolved.items():
                if dt == "sales":
                    continue
                loaded[dt] = load_dataset(dt, src)

            sales_records = loaded["sales"]["records"]
            from analyzer import (
                enrich_sales,
                normalize_text,
            )
            article_map       = {r["product_key"]: r for r in loaded.get("articles", {}).get("records", []) if r.get("product_key")}
            route_seller_map  = {normalize_text(r["seller_name"]): r for r in loaded.get("routes", {}).get("records", []) if r.get("seller_name")}
            seller_key_map    = {r["seller_key"]: r for r in loaded.get("sellers", {}).get("records", []) if r.get("seller_key")}
            seller_name_map   = {normalize_text(r["seller_name"]): r for r in loaded.get("sellers", {}).get("records", []) if r.get("seller_name")}
            seller_route_map  = {normalize_text(r["route_description"]): r for r in loaded.get("sellers", {}).get("records", []) if r.get("route_description")}

            enriched = enrich_sales(sales_records, article_map, route_seller_map,
                                    seller_key_map, seller_name_map, seller_route_map)

            _, filtered = apply_filters(enriched, data.get("filters", {}))

            schema = detect_schema(filtered)
            tasks  = resolve_tasks(schema)
            task   = next((t for t in tasks if t["id"] == task_id), None)
            if task is None:
                self.send_json({"error": f"El análisis '{task_id}' no está disponible con los datos actuales"}, status=422)
                return

            engine = AnalysisEngine()
            result = engine.run_task_with_combo(task, filtered, combo)
            kpi_set    = generate_kpis({task_id: result}, [task])
            viz_spec   = build_viz(task, result)
            dyn_ins    = write_insights(kpi_set, {task_id: result}, [task])
            self.send_json({
                "task_id":  task_id,
                "combo":    combo,
                "result":   result,
                "kpiSet":   kpi_set,
                "vizSpec":  viz_spec,
                "insights": dyn_ins,
                "insightsSummary": insights_summary(dyn_ins),
            })
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=422)
        except Exception as exc:
            _log_error("analyze_dynamic", exc, {"task_id": task_id, "combo": combo})
            self.send_json({"error": str(exc)}, status=500)

    def handle_analyze(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        datasets = data.get("datasets", {})
        if not datasets:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return

        try:
            resolved = _resolve_datasets(datasets)
            result = analyze_datasets(
                resolved,
                filters=data.get("filters", {}),
                supplier_focus=data.get("supplierFocus"),
            )
        except ValueError as exc:
            _log_error("analyze_validation", exc, {"filters": data.get("filters", {}), "datasets": list(datasets.keys())})
            self.send_json({"error": str(exc)}, status=422)
            return
        except Exception as exc:
            _log_error("analyze", exc, {"filters": data.get("filters", {}), "datasets": list(datasets.keys())})
            self.send_json({"error": str(exc)}, status=500)
            return

        # Guardar en MongoDB Atlas (colección "registros")
        db = get_db()
        if db is not None:
            try:
                db["registros"].insert_one({
                    "timestamp": datetime.now(timezone.utc),
                    "filters": data.get("filters", {}),
                    "meta": result.get("meta", {}),
                    "summary": result.get("summary", {}),
                    "insightsSummary": result.get("insightsSummary"),
                })
            except Exception:
                pass  # No bloquear la respuesta si falla MongoDB

        # Persistir configuración de datasets en sesión
        save_session(data.get("datasets", {}))

        self.send_json(result)

    def handle_get_session(self):
        """GET /api/session — devuelve la última configuración de datasets guardada."""
        session = load_session()
        if session:
            self.send_json(session)
        else:
            self.send_json({"datasets": None})

    def handle_save_session(self):
        """POST /api/session — persiste la configuración de datasets."""
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        ok = save_session(data.get("datasets", {}))
        self.send_json({"saved": ok})

    def handle_list_analyses(self, parsed):
        db = get_db()
        if db is None:
            self.send_json({"error": "MongoDB no configurado"}, status=503)
            return
        query = parse_qs(parsed.query)
        limit = min(int(query.get("limit", [50])[0]), 200)
        cursor = db["registros"].find({}, {"_id": 0}).sort("timestamp", -1).limit(limit)
        self.send_json({"analyses": list(cursor)})

    def serve_file(self, path, content_type):
        if not path.exists():
            self.send_error(404, "Archivo no encontrado")
            return
        data = path.read_bytes()
        self.send_response(200)
        self._send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)

    def serve_vendor_file(self, request_path):
        vendor_root = VENDOR_DIR.resolve()
        relative = request_path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if vendor_root != target and vendor_root not in target.parents:
            self.send_error(404, "Ruta no encontrada")
            return
        if target.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        else:
            content_type = "application/octet-stream"
        self.serve_file(target, content_type)

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return


def list_available_files(scope="uploads"):
    found = []
    seen = set()

    roots = [UPLOAD_DIR] if scope == "uploads" else ALLOWED_ROOTS
    for root in roots:
        if not Path(root).exists():
            continue
        for item in iter_excel_files(root, MAX_SCAN_DEPTH):
            try:
                relative_to_parent = item.relative_to(PARENT_DIR)
            except ValueError:
                relative_to_parent = item.name
            path = str(relative_to_parent)
            if path in seen:
                continue
            seen.add(path)
            found.append(
                {
                    "name": item.name,
                    "path": path,
                    "location": format_location(relative_to_parent, scope),
                }
            )

    return sorted(found, key=lambda item: (item["location"], item["name"].lower()))


def format_location(relative_to_parent, scope):
    parent = str(relative_to_parent.parent)
    if scope == "uploads":
        return "Subidos"
    if parent == ".":
        return "Proyecto"
    return parent


def build_dataset_schema():
    items = {}
    for dataset_type, definition in DATASET_DEFINITIONS.items():
        items[dataset_type] = {
            "label": definition["label"],
            "required": definition["required"],
            "multipleSources": dataset_type == "sales",
            "fields": [
                {"id": field_id, "label": field_meta["label"], "required": field_meta["required"]}
                for field_id, field_meta in definition["fields"].items()
            ],
        }
    return items


def iter_excel_files(root, max_depth):
    root = Path(root).resolve()
    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        depth = len(current_path.relative_to(root).parts)
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [name for name in dirnames if name not in {".git", "__pycache__", "static"}]
        for filename in filenames:
            item = current_path / filename
            if item.suffix.lower() in ALLOWED_EXTENSIONS:
                yield item


def save_uploaded_file(item):
    UPLOAD_DIR.mkdir(exist_ok=True)
    original_name = Path(item.filename).name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Tipo de archivo no permitido: {original_name}")

    target = UPLOAD_DIR / original_name   # sobrescribir si ya existe
    with target.open("wb") as handle:
        while True:
            chunk = item.file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    return {
        "name": target.name,
        "path": f"uploads/{target.name}",
        "location": "Subidos",
    }


def unique_upload_path(filename):
    candidate = UPLOAD_DIR / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = UPLOAD_DIR / f"{stem}_{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def _resolve_datasets(datasets):
    """
    Convierte el payload de datasets del frontend en paths reales validados.
    Reutilizado tanto por handle_analyze como por handle_analyze_dynamic.
    """
    from analyzer import DATASET_DEFINITIONS
    resolved = {}
    sales_source = None
    erp_cookie = None
    for dataset_type, config in datasets.items():
        if dataset_type not in DATASET_DEFINITIONS:
            continue
        if dataset_type == "sales":
            erp_config = config.get("erp") if isinstance(config.get("erp"), dict) else {}
            use_auto = config.get("source") == "auto"
            use_mongo = config.get("source") == "mongo"
            use_clickhouse = config.get("source") == "clickhouse"
            use_erp = not use_mongo and not use_clickhouse and (
                config.get("source") == "erp" or erp_config.get("enabled")
            )
            fecha_desde = config.get("fechaDesde") or erp_config.get("fechaDesde")
            fecha_hasta = config.get("fechaHasta") or erp_config.get("fechaHasta")
            analysis_window = None
            if fecha_desde and fecha_hasta and (use_auto or use_mongo or use_clickhouse or use_erp):
                analysis_window = _build_sales_analysis_window(fecha_desde, fecha_hasta)
            if use_auto:
                dataset, sales_source = _load_commercial_sales_dataset(analysis_window["loadStart"], analysis_window["loadEnd"])
                resolved[dataset_type] = _attach_sales_analysis_window(dataset, analysis_window)
                continue
            if use_erp:
                erp_cookie = erp_cookie or erp_login().get("cookie")
                dataset = _fetch_sales_dataset_chunked(analysis_window["loadStart"], analysis_window["loadEnd"], detailed=True, cookie=erp_cookie)
                resolved[dataset_type] = _attach_sales_analysis_window(dataset, analysis_window)
                sales_source = "erp"
                continue
            if use_mongo:
                dataset = load_erp_sales_dataset(analysis_window["loadStart"], analysis_window["loadEnd"])
                resolved[dataset_type] = _attach_sales_analysis_window(dataset, analysis_window)
                sales_source = "mongo"
                continue
            if use_clickhouse:
                dataset = load_erp_sales_dataset_clickhouse(analysis_window["loadStart"], analysis_window["loadEnd"])
                resolved[dataset_type] = _attach_sales_analysis_window(dataset, analysis_window)
                sales_source = "clickhouse"
                continue
            sources = []
            for source in config.get("sources", []):
                filename  = source.get("file")
                sheet_name = source.get("sheet")
                if not filename or not sheet_name:
                    continue
                target = safe_file_path(filename)
                if not target.exists():
                    raise FileNotFoundError(f"No se encontró {filename}")
                sources.append({
                    "file":      filename,
                    "path":      target,
                    "sheet":     sheet_name,
                    "headerRow": int(source.get("headerRow", 0)),
                })
            if not sources:
                continue
            resolved[dataset_type] = {
                "sources": sources,
                "mapping": config.get("mapping", {}),
            }
            sales_source = "files"
        else:
            filename  = config.get("file")
            sheet_name = config.get("sheet")
            if not filename or not sheet_name:
                continue
            target = safe_file_path(filename)
            if not target.exists():
                raise FileNotFoundError(f"No se encontró {filename}")
            resolved[dataset_type] = {
                "file":      filename,
                "path":      target,
                "sheet":     sheet_name,
                "headerRow": int(config.get("headerRow", 0)),
                "mapping":   config.get("mapping", {}),
            }
    if "articles" not in resolved and sales_source in {"erp", "mongo", "clickhouse"}:
        try:
            if sales_source == "erp":
                erp_cookie = erp_cookie or erp_login().get("cookie")
                resolved["articles"] = fetch_articles_dataset(cookie=erp_cookie)
            else:
                resolved["articles"] = load_erp_articles_dataset()
        except Exception:
            pass
    if "sellers" not in resolved and sales_source in {"erp", "mongo", "clickhouse"}:
        try:
            if sales_source == "erp":
                erp_cookie = erp_cookie or erp_login().get("cookie")
                resolved["sellers"] = fetch_staff_dataset(cookie=erp_cookie)
            else:
                resolved["sellers"] = load_erp_sellers_dataset()
        except Exception:
            if sales_source == "erp":
                try:
                    records = derive_sellers_records(resolved["sales"].get("records", []))
                    resolved["sellers"] = build_dataset("sellers", "ChessERP vendedores derivados", "Ventas ERP", records, source_kind="erp")
                except Exception:
                    pass
    if "routes" not in resolved and sales_source in {"erp", "mongo", "clickhouse"}:
        try:
            if sales_source == "erp":
                erp_cookie = erp_cookie or erp_login().get("cookie")
                resolved["routes"] = fetch_routes_dataset(cookie=erp_cookie)
            else:
                resolved["routes"] = load_erp_routes_dataset()
        except Exception:
            if sales_source == "erp":
                try:
                    records = derive_routes_records(resolved["sales"].get("records", []))
                    resolved["routes"] = build_dataset("routes", "ChessERP rutas derivadas", "Ventas ERP", records, source_kind="erp")
                except Exception:
                    pass
    return resolved


def _load_commercial_sales_dataset(fecha_desde: str, fecha_hasta: str):
    if not fecha_desde or not fecha_hasta:
        raise ValueError("Elegí fecha desde y fecha hasta para consultar la base comercial.")

    mongo_error = None
    clickhouse_error = None

    try:
        return load_erp_sales_dataset(fecha_desde, fecha_hasta, require_coverage=True), "mongo"
    except Exception as exc:
        mongo_error = exc

    try:
        return load_erp_sales_dataset_clickhouse(fecha_desde, fecha_hasta), "clickhouse"
    except Exception as exc:
        clickhouse_error = exc

    if mongo_error and not clickhouse_error:
        raise ValueError(str(mongo_error))
    if clickhouse_error and not mongo_error:
        raise ValueError(str(clickhouse_error))
    raise ValueError(
        f"La base comercial no tiene información disponible para el rango {fecha_desde} a {fecha_hasta}."
    )


def safe_file_path(filename):
    candidate = (PARENT_DIR / filename).resolve()
    if not any(root == candidate or root in candidate.parents for root in ALLOWED_ROOTS):
        raise ValueError("Ruta inválida")
    return candidate


def _erp_masters_available(storage):
    return bool(
        storage.get("articleRecords")
        and storage.get("sellerRecords")
        and storage.get("routeRecords")
        and storage.get("marketingRecords")
    )


def _log_error(context, exc, extra=None):
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with ERROR_LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "context": context,
                        "error": str(exc),
                        "extra": extra or {},
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                )
            )
            handle.write("\n")
    except Exception:
        pass


def _read_recent_errors(limit=20):
    if not ERROR_LOG.exists():
        return []
    items = []
    try:
        with ERROR_LOG.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                items.append(
                    {
                        "timestamp": payload.get("timestamp"),
                        "context": payload.get("context"),
                        "error": payload.get("error"),
                        "extra": payload.get("extra") or {},
                    }
                )
    except Exception:
        return []
    return list(reversed(items[-limit:]))


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    UPLOAD_DIR.mkdir(exist_ok=True)
    print(f"Servidor local activo en http://{HOST}:{PORT}")
    print("Cargá venta por cliente y sus maestros para relacionar ventas, artículos, rutas y vendedores.")
    if ping():
        print("MongoDB Atlas conectado — DB: Intel-Comercial")
    else:
        print("MongoDB no conectado (revisá MONGO_URI en .env)")
    try:
        ReusableHTTPServer((HOST, PORT), AppHandler).serve_forever()
    except OSError as exc:
        if exc.errno == 48:
            print(f"El puerto {PORT} ya está en uso. Cerrá la otra instancia de la app o reutilizá la que ya está corriendo en http://{HOST}:{PORT}.")
        raise
