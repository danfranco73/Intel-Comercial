import json
import os
import traceback
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from analyzer import DATASET_DEFINITIONS, suggest_mappings
from schema_detector import detect_schema_from_raw
from rule_engine import resolve_tasks, tasks_by_domain
from clickhouse_client import (
    get_clickhouse_storage_status,
    load_erp_sales_dataset_clickhouse,
    sync_erp_sales_clickhouse,
)
from mongo_client import (
    get_db,
    get_erp_prefilter_options,
    get_erp_sales_uncovered_ranges,
    get_erp_storage_status,
    get_mongo_sales_retention_cutoff,
    load_erp_articles_dataset,
    load_erp_routes_dataset,
    load_erp_sales_dataset,
    load_erp_sellers_dataset,
    ping,
    record_erp_sync_summary,
    sync_erp_sales,
    sync_erp_sales_catalogs,
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
from security.auth import ensure_auth_indexes
from security.authorization import restrict_filter_options
from security.audit import configure_security_logging
from sales_coach.config import (
    erp_sales_chunk_days,
    load_app_config,
    max_json_body_bytes,
    max_upload_body_bytes,
)
from sales_coach.repositories import AuthRepository, SalesRepository
from sales_coach.schemas import SyncRequest
from sales_coach.services import AuthService, SyncService
from sales_coach.routes import GET_ROUTES, POST_ROUTES
from sales_coach.routes.auth_routes import AuthRoutesMixin
from sales_coach.routes.analysis_routes import AnalysisRoutesMixin
from sales_coach.routes.operations_routes import OperationsRoutesMixin
from sales_coach.routes.objective_routes import ObjectiveRoutesMixin
from sales_coach.routes.sales_coach_routes import SalesCoachRoutesMixin
from sales_coach.routes.coach_rule_routes import CoachRuleRoutesMixin
from sales_coach.routes.alert_routes import AlertRoutesMixin
from sales_coach.routes.meeting_routes import MeetingRoutesMixin


CONFIG = load_app_config()
BASE_DIR = CONFIG.base_dir
PARENT_DIR = CONFIG.parent_dir
STATIC_DIR = CONFIG.static_dir
VENDOR_DIR = CONFIG.vendor_dir
UPLOAD_DIR = CONFIG.upload_dir
LOG_DIR = CONFIG.log_dir
ERROR_LOG = CONFIG.error_log
configure_security_logging(LOG_DIR)
HOST = CONFIG.host
PORT = CONFIG.port
ALLOWED_EXTENSIONS = CONFIG.allowed_extensions
ALLOWED_ROOTS = CONFIG.allowed_roots
MAX_SCAN_DEPTH = CONFIG.max_scan_depth


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _erp_sales_chunk_days():
    return erp_sales_chunk_days()


def _parse_iso_date(value, field_name):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} debe tener formato YYYY-MM-DD")


def _build_sales_analysis_window(
    fecha_desde, fecha_hasta, comparison_desde=None, comparison_hasta=None
):
    start = _parse_iso_date(fecha_desde, "fechaDesde")
    end = _parse_iso_date(fecha_hasta, "fechaHasta")
    if start > end:
        raise ValueError("fechaDesde no puede ser mayor que fechaHasta")
    days = (end - start).days + 1
    if comparison_desde or comparison_hasta:
        if not comparison_desde or not comparison_hasta:
            raise ValueError("La comparación requiere fecha desde y fecha hasta")
        comparison_start = _parse_iso_date(comparison_desde, "comparisonDesde")
        comparison_end = _parse_iso_date(comparison_hasta, "comparisonHasta")
        if comparison_start > comparison_end:
            raise ValueError("comparisonDesde no puede ser mayor que comparisonHasta")
    else:
        comparison_end = start - timedelta(days=1)
        comparison_start = comparison_end - timedelta(days=days - 1)
    return {
        "selectedStart": start.isoformat(),
        "selectedEnd": end.isoformat(),
        "comparisonStart": comparison_start.isoformat(),
        "comparisonEnd": comparison_end.isoformat(),
        "loadStart": min(comparison_start, start).isoformat(),
        "loadEnd": max(comparison_end, end).isoformat(),
        "days": days,
    }


def _build_tactical_month_window(fecha_desde, fecha_hasta):
    base_window = _build_sales_analysis_window(fecha_desde, fecha_hasta)
    selected_end = _parse_iso_date(base_window["selectedEnd"], "fechaHasta")
    previous_month_start = (selected_end.replace(day=1) - timedelta(days=1)).replace(day=1)
    previous_year_start = date(selected_end.year - 1, selected_end.month, 1)
    load_start = min(_parse_iso_date(base_window["loadStart"], "loadStart"), previous_month_start, previous_year_start)
    return {
        **base_window,
        "loadStart": load_start.isoformat(),
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


def _max_json_body_bytes():
    return max_json_body_bytes()


def _max_upload_body_bytes():
    return max_upload_body_bytes()


def _client_ip(handler):
    forwarded = (handler.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return handler.client_address[0]


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


def _parse_multipart_files(headers, body, field_name="files"):
    content_type = headers.get("Content-Type", "")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    if not message.is_multipart():
        return []

    files = []
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        if part.get_param("name", header="content-disposition") != field_name:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        files.append(SimpleNamespace(filename=filename, file=BytesIO(payload)))
    return files


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


def _sync_sales_range_chunked(fecha_desde, fecha_hasta, cookie=None, force_refresh=False):
    uncovered_ranges = [(fecha_desde, fecha_hasta)] if force_refresh else get_erp_sales_uncovered_ranges(fecha_desde, fecha_hasta)
    reused_ranges = _invert_ranges(fecha_desde, fecha_hasta, uncovered_ranges)
    if not uncovered_ranges:
        summary = {
            "range": {"fechaDesde": fecha_desde, "fechaHasta": fecha_hasta},
            "rowsRead": 0,
            "rowsValid": 0,
            "recordsReceived": 0,
            "rowVersions": 0,
            "deleted": 0,
            "upserted": 0,
            "modified": 0,
            "matched": 0,
            "mongoStored": 0,
            "clickhouseStored": 0,
            "mongoChunkCount": 0,
            "chunkCount": 0,
            "chunkDays": _erp_sales_chunk_days(),
            "warning": None,
            "warnings": [],
            "chunks": [],
            "origin": "api_sync_batch",
            "reusedExistingRange": True,
            "forceRefresh": force_refresh,
            "fetchedRanges": [],
            "reusedCoveredRanges": reused_ranges,
            "message": "El rango solicitado ya estaba cubierto en la base persistida. No se consultó nuevamente ChessERP.",
        }
        return record_erp_sync_summary("sales_batch", summary)

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

    chunk_index = 0
    for uncovered_start, uncovered_end in uncovered_ranges:
        for chunk_start, chunk_end in _iter_date_chunks(uncovered_start, uncovered_end):
            chunk_index += 1
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
                "index": chunk_index,
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
            if os.getenv("APP_SYNC_PROGRESS_STDOUT", "").strip().lower() in {"1", "true", "yes"}:
                print(
                    f"[sync] tramo {chunk_index}: {chunk_start} a {chunk_end} "
                    f"rows={dataset.get('rowsValid', 0)} clickhouse={chunk_summary['clickhouseStored']} "
                    f"mongo={chunk_summary['mongoStored']}",
                    flush=True,
                )
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
        "reusedExistingRange": False,
        "forceRefresh": force_refresh,
        "fetchedRanges": [{"fechaDesde": start, "fechaHasta": end} for start, end in uncovered_ranges],
        "reusedCoveredRanges": reused_ranges,
    }
    return record_erp_sync_summary("sales_batch", summary)


def _invert_ranges(fecha_desde, fecha_hasta, uncovered_ranges):
    start = _parse_iso_date(fecha_desde, "fechaDesde")
    end = _parse_iso_date(fecha_hasta, "fechaHasta")
    if not uncovered_ranges:
        return [{"fechaDesde": fecha_desde, "fechaHasta": fecha_hasta}]
    covered = []
    cursor = start
    for current_start, current_end in uncovered_ranges:
        range_start = _parse_iso_date(current_start, "fechaDesde")
        range_end = _parse_iso_date(current_end, "fechaHasta")
        if cursor < range_start:
            covered.append({"fechaDesde": cursor.isoformat(), "fechaHasta": (range_start - timedelta(days=1)).isoformat()})
        cursor = max(cursor, range_end + timedelta(days=1))
    if cursor <= end:
        covered.append({"fechaDesde": cursor.isoformat(), "fechaHasta": end.isoformat()})
    return covered


class AppHandler(
    AuthRoutesMixin,
    AnalysisRoutesMixin,
    OperationsRoutesMixin,
    ObjectiveRoutesMixin,
    SalesCoachRoutesMixin,
    CoachRuleRoutesMixin,
    AlertRoutesMixin,
    MeetingRoutesMixin,
    BaseHTTPRequestHandler,
):
    auth_context = None
    data_scope = None

    def _auth_service(self):
        db = get_db()
        return AuthService(AuthRepository(db)) if db is not None else None

    def database(self):
        return get_db()

    def request_ip(self):
        return _client_ip(self)

    def log_application_error(self, context, exc, extra=None):
        _log_error(
            context,
            exc,
            {
                **(extra or {}),
                "requestId": self.get_request_id(),
                "endpoint": self.path,
                "userId": self.auth_context.user.id if self.auth_context else None,
            },
        )

    def resolve_datasets(self, datasets):
        return _resolve_datasets(datasets)

    def _send_security_headers(self):
        self.send_header("X-Request-ID", self.get_request_id())
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'",
        )

    def get_request_id(self):
        existing = getattr(self, "_request_id", None)
        if existing:
            return existing
        candidate = str(self.headers.get("X-Request-ID") or "").strip()
        if not candidate or len(candidate) > 128:
            candidate = uuid4().hex
        self._request_id = candidate
        return candidate

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

    def _require_authentication(self, permission="commercial.read"):
        service = self._auth_service()
        if service is None:
            self._send_json_error("La autenticación no está disponible porque MongoDB no está configurado.", 503)
            return False
        try:
            context, data_scope = service.authenticate_request(
                self.headers.get("Cookie"),
                permission,
            )
        except LookupError as exc:
            self._send_json_error(str(exc), 401)
            return False
        except PermissionError as exc:
            self._send_json_error(str(exc), 403)
            return False
        self.auth_context = context
        self.data_scope = data_scope
        return True

    def _require_csrf(self):
        service = self._auth_service()
        token = self.headers.get("X-CSRF-Token")
        if service is None or self.auth_context is None or not service.repository.verify_csrf(self.auth_context, token):
            self._send_json_error("Token CSRF inválido o ausente.", 403)
            return False
        return True

    def _redirect(self, location):
        self.send_response(302)
        self._send_security_headers()
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _require_page_authentication(self, permission="commercial.read"):
        service = self._auth_service()
        if service is None:
            self._redirect("/login?error=auth_unavailable")
            return False
        try:
            context, data_scope = service.authenticate_request(
                self.headers.get("Cookie"),
                permission,
            )
        except LookupError:
            self._redirect("/login")
            return False
        except PermissionError:
            self.send_error(403, "Acceso denegado")
            return False
        self.auth_context = context
        self.data_scope = data_scope
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self._send_security_headers()
            self.end_headers()
            return
        if parsed.path == "/login":
            self.serve_file(STATIC_DIR / "login.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/login.js":
            self.serve_file(STATIC_DIR / "login.js", "application/javascript; charset=utf-8")
            return
        if parsed.path in {"/", "/bi", "/sales-coach", "/sales-coach/seller", "/sales-coach/client"}:
            if not self._require_page_authentication("commercial.read"):
                return
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/admin":
            if not self._require_page_authentication("admin"):
                return
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
        if parsed.path == "/api/files":
            if not self._require_authentication("admin"):
                return
            query = parse_qs(parsed.query)
            scope = query.get("scope", ["uploads"])[0]
            self.send_json({"files": list_available_files(scope=scope)})
            return
        if parsed.path == "/api/datasets":
            if not self._require_authentication():
                return
            self.send_json({"datasets": build_dataset_schema()})
            return
        definition = GET_ROUTES.get(parsed.path)
        if definition:
            if not self._require_authentication(definition.permission):
                return
            handler = getattr(self, definition.handler)
            handler(parsed) if definition.pass_parsed_url else handler()
            return
        self.send_error(404, "Ruta no encontrada")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/login":
            self.handle_login()
            return
        if parsed.path == "/api/db-status":
            if not self._require_authentication("admin") or not self._require_csrf():
                return
            self.send_json({"connected": ping()})
            return
        definition = POST_ROUTES.get(parsed.path)
        if definition:
            if not self._require_authentication(definition.permission):
                return
            if definition.csrf and not self._require_csrf():
                return
            getattr(self, definition.handler)()
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
        seller_names = (self.data_scope or {}).get("seller_name")
        filters = get_erp_prefilter_options(seller_names=seller_names)
        self.send_json({"filters": restrict_filter_options(filters, self.data_scope)})

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
        try:
            result = SyncService(
                _sync_sales_range_chunked,
                _erp_masters_available,
            ).run(
                data,
                requested_by=self.auth_context.user.id,
                origin="api",
            )
            self.send_json(result)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            _log_error(
                "erp_sync",
                exc,
                {
                    "fechaDesde": data.get("fechaDesde"),
                    "fechaHasta": data.get("fechaHasta"),
                    "refreshMasters": bool(data.get("refreshMasters")),
                    "forceRefreshSales": bool(data.get("forceRefreshSales")),
                },
            )
            self.send_json({"error": str(exc)}, status=500)

    def handle_erp_sync_access_catalogs(self):
        data = {}
        try:
            data = self._read_json_body()
            start = _parse_iso_date(data.get("fechaDesde"), "fechaDesde")
            end = _parse_iso_date(data.get("fechaHasta"), "fechaHasta")
            if start > end:
                raise ValueError("fechaDesde no puede ser mayor que fechaHasta")
            dataset = fetch_sales_dataset(
                start.isoformat(),
                end.isoformat(),
                detailed=False,
                cookie=erp_login().get("cookie"),
            )
            result = sync_erp_sales_catalogs(
                dataset.get("records") or [],
                origin="admin_access_catalog_sync",
            )
            self.send_json({
                "catalogs": result,
                "rowsRead": dataset.get("rowsRead", 0),
                "range": {"fechaDesde": start.isoformat(), "fechaHasta": end.isoformat()},
            })
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self.log_application_error("erp_access_catalog_sync", exc)
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
        if self.headers.get_content_type() != "multipart/form-data":
            self.send_json({"error": "El upload debe enviarse como multipart/form-data"}, status=400)
            return

        body = self.rfile.read(content_length)
        items = _parse_multipart_files(self.headers, body)
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

    def send_json(self, payload, status=200, headers=None):
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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
            exact_range_only = bool(config.get("exactRangeOnly"))
            load_strategy = config.get("loadStrategy")
            allow_partial_coverage = bool(config.get("allowPartialCoverage"))
            use_erp = not use_mongo and not use_clickhouse and (
                config.get("source") == "erp" or erp_config.get("enabled")
            )
            fecha_desde = config.get("fechaDesde") or erp_config.get("fechaDesde")
            fecha_hasta = config.get("fechaHasta") or erp_config.get("fechaHasta")
            comparison_desde = config.get("comparisonDesde")
            comparison_hasta = config.get("comparisonHasta")
            analysis_window = None
            if fecha_desde and fecha_hasta and (use_auto or use_mongo or use_clickhouse or use_erp):
                if load_strategy == "tactical_month":
                    analysis_window = _build_tactical_month_window(fecha_desde, fecha_hasta)
                else:
                    analysis_window = _build_sales_analysis_window(
                        fecha_desde,
                        fecha_hasta,
                        comparison_desde,
                        comparison_hasta,
                    )
                if exact_range_only:
                    analysis_window = {
                        **analysis_window,
                        "loadStart": analysis_window["selectedStart"],
                        "loadEnd": analysis_window["selectedEnd"],
                    }
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
                dataset = load_erp_sales_dataset(
                    analysis_window["loadStart"],
                    analysis_window["loadEnd"],
                    require_coverage=not allow_partial_coverage,
                )
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
            if sales_source in {"mongo", "clickhouse"}:
                try:
                    erp_cookie = erp_cookie or erp_login().get("cookie")
                    resolved["articles"] = fetch_articles_dataset(cookie=erp_cookie)
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
            elif sales_source in {"mongo", "clickhouse"}:
                try:
                    erp_cookie = erp_cookie or erp_login().get("cookie")
                    resolved["sellers"] = fetch_staff_dataset(cookie=erp_cookie)
                except Exception:
                    pass
    if "routes" not in resolved and sales_source in {"erp", "mongo", "clickhouse"}:
        try:
            if sales_source == "erp":
                erp_cookie = erp_cookie or erp_login().get("cookie")
                resolved["routes"] = fetch_routes_dataset(cookie=erp_cookie)
            else:
                resolved["routes"] = load_erp_routes_dataset()
                if not _routes_have_client_keys(resolved["routes"]):
                    try:
                        erp_cookie = erp_cookie or erp_login().get("cookie")
                        live_routes = fetch_routes_dataset(cookie=erp_cookie)
                        if _routes_have_client_keys(live_routes):
                            resolved["routes"] = live_routes
                    except Exception:
                        pass
        except Exception:
            if sales_source == "erp":
                try:
                    records = derive_routes_records(resolved["sales"].get("records", []))
                    resolved["routes"] = build_dataset("routes", "ChessERP rutas derivadas", "Ventas ERP", records, source_kind="erp")
                except Exception:
                    pass
            elif sales_source in {"mongo", "clickhouse"}:
                try:
                    erp_cookie = erp_cookie or erp_login().get("cookie")
                    resolved["routes"] = fetch_routes_dataset(cookie=erp_cookie)
                except Exception:
                    pass
    return resolved


def _routes_have_client_keys(dataset):
    return any(item.get("client_keys") for item in (dataset or {}).get("records", []))


def _load_commercial_sales_dataset(fecha_desde: str, fecha_hasta: str):
    if not fecha_desde or not fecha_hasta:
        raise ValueError("Elegí fecha desde y fecha hasta para consultar la base comercial.")
    return SalesRepository().load_preferred(fecha_desde, fecha_hasta)


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


def main():
    os.chdir(BASE_DIR)
    UPLOAD_DIR.mkdir(exist_ok=True)
    print(f"Servidor local activo en http://{HOST}:{PORT}")
    print("Cargá venta por cliente y sus maestros para relacionar ventas, artículos, rutas y vendedores.")
    if ping():
        ensure_auth_indexes(get_db())
        print("MongoDB Atlas conectado — DB: Intel-Comercial")
    else:
        print("MongoDB no conectado (revisá MONGO_URI en .env)")
    try:
        ReusableHTTPServer((HOST, PORT), AppHandler).serve_forever()
    except OSError as exc:
        if exc.errno == 48:
            print(f"El puerto {PORT} ya está en uso. Cerrá la otra instancia de la app o reutilizá la que ya está corriendo en http://{HOST}:{PORT}.")
        raise


if __name__ == "__main__":
    main()
