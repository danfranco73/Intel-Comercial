import cgi
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from analyzer import DATASET_DEFINITIONS, analyze_datasets, suggest_mappings
from schema_detector import detect_schema_from_raw
from rule_engine import resolve_tasks, tasks_by_domain
from analysis_engine import AnalysisEngine
from kpi_generator import generate_kpis
from viz_selector import build_all_viz, build_viz
from insight_writer import write_insights, insights_summary
from mongo_client import get_db, ping, save_session, load_session
from xlsx_reader import preview_sheet, read_sheet_names


BASE_DIR = Path(__file__).resolve().parent
PARENT_DIR = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
HOST = "127.0.0.1"
PORT = 8765
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}
ALLOWED_ROOTS = (BASE_DIR, PARENT_DIR)
MAX_SCAN_DEPTH = 2


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.css":
            self.serve_file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self.serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/api/session":
            self.handle_get_session()
            return
        if parsed.path == "/api/files":
            query = parse_qs(parsed.query)
            scope = query.get("scope", ["uploads"])[0]
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
            self.handle_list_analyses(parsed)
            return
        if parsed.path == "/api/workbook":
            self.handle_workbook(parsed)
            return
        if parsed.path == "/api/preview":
            self.handle_preview(parsed)
            return
        self.send_error(404, "Ruta no encontrada")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/upload":
            self.handle_upload()
            return
        if parsed.path == "/api/clear-uploads":
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

    def handle_upload(self):
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

        length  = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        data    = json.loads(payload or "{}")

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
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def handle_analyze(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        data = json.loads(payload or "{}")
        datasets = data.get("datasets", {})
        if not datasets:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return

        try:
            resolved = {}
            for dataset_type, config in datasets.items():
                if dataset_type not in DATASET_DEFINITIONS:
                    continue
                if dataset_type == "sales":
                    sources = []
                    for source in config.get("sources", []):
                        filename = source.get("file")
                        sheet_name = source.get("sheet")
                        if not filename or not sheet_name:
                            continue
                        target = safe_file_path(filename)
                        if not target.exists():
                            raise FileNotFoundError(f"No se encontró {filename}")
                        sources.append(
                            {
                                "file": filename,
                                "path": target,
                                "sheet": sheet_name,
                                "headerRow": int(source.get("headerRow", 0)),
                            }
                        )
                    if not sources:
                        continue
                    resolved[dataset_type] = {
                        "sources": sources,
                        "mapping": config.get("mapping", {}),
                    }
                    continue
                filename = config.get("file")
                sheet_name = config.get("sheet")
                if not filename or not sheet_name:
                    continue
                target = safe_file_path(filename)
                if not target.exists():
                    raise FileNotFoundError(f"No se encontró {filename}")
                resolved[dataset_type] = {
                    "file": filename,
                    "path": target,
                    "sheet": sheet_name,
                    "headerRow": int(config.get("headerRow", 0)),
                    "mapping": config.get("mapping", {}),
                }
            result = analyze_datasets(resolved, filters=data.get("filters", {}))
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)
            return

        # Guardar en MongoDB Atlas (colección "registros")
        db = get_db()
        if db is not None:
            try:
                db["registros"].insert_one({
                    "timestamp": datetime.now(timezone.utc),
                    "filters": data.get("filters", {}),
                    "result": result,
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
        length  = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length).decode("utf-8")
        data    = json.loads(payload or "{}")
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
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
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
    for dataset_type, config in datasets.items():
        if dataset_type not in DATASET_DEFINITIONS:
            continue
        if dataset_type == "sales":
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
    return resolved


def safe_file_path(filename):
    candidate = (PARENT_DIR / filename).resolve()
    if not any(root == candidate or root in candidate.parents for root in ALLOWED_ROOTS):
        raise ValueError("Ruta inválida")
    return candidate


if __name__ == "__main__":
    os.chdir(BASE_DIR)
    UPLOAD_DIR.mkdir(exist_ok=True)
    print(f"Servidor local activo en http://{HOST}:{PORT}")
    print("Cargá venta por cliente y sus maestros para relacionar ventas, artículos, rutas y vendedores.")
    if ping():
        print("MongoDB Atlas conectado — DB: Intel-Comercial")
    else:
        print("MongoDB no conectado (revisá MONGO_URI en .env)")
    HTTPServer((HOST, PORT), AppHandler).serve_forever()
