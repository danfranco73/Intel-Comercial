"""Read-only Chess capture with private local checkpoints; no production writes."""
import fcntl
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import ssl
import time
from datetime import datetime, timezone, date, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from sales_coach.domain.sales_v2 import canonical, multiset_hash


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


class Capture:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def get(self, endpoint, params, destination):
        from erp_client import get_erp_config, erp_login, invalidate_erp_session
        if endpoint not in ("/ventas/", "/articulos/", "/stock/"):
            raise ValueError("Endpoint outside read-only pilot allowlist")
        cfg = get_erp_config()
        destination = Path(destination)
        if destination.exists() and any(destination.glob("*.meta.json")):
            raise ValueError("A capture destination is immutable; allocate a new attempt")
        destination.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, 6):
            started = now()
            tick = time.monotonic()
            meta = {"endpoint": endpoint, "params": params, "started_at": started, "attempt": attempt}
            meta["source_instance_sha256"] = hashlib.sha256(cfg["base_url"].encode()).hexdigest()
            try:
                cookie = erp_login()["cookie"]
                request = Request(cfg["base_url"] + endpoint + "?" + urlencode(params), method="GET",
                                  headers={"Accept": "application/json", "Cookie": cookie})
                context = None if cfg["verify_ssl"] else ssl._create_unverified_context()
                with urlopen(request, timeout=min(cfg["timeout"], 180), context=context) as response:
                    payload = response.read()
                    meta["http_status"] = response.status
                body = json.loads(payload)
                meta.update(finished_at=now(), seconds=time.monotonic()-tick, bytes=len(payload),
                            sha256=hashlib.sha256(payload).hexdigest(), success=True)
                with gzip.open(destination/f"attempt-{attempt}.json.gz", "wb") as f:
                    f.write(payload)
                atomic_json(destination/f"attempt-{attempt}.meta.json", meta)
                time.sleep(0.5)
                return body, meta, str((destination/f"attempt-{attempt}.json.gz").relative_to(self.root))
            except Exception as exc:
                meta.update(finished_at=now(), seconds=time.monotonic()-tick, success=False, error_type=type(exc).__name__)
                if isinstance(exc, HTTPError):
                    meta["http_status"] = exc.code
                    if exc.code in (401,403):
                        invalidate_erp_session()
                atomic_json(destination/f"attempt-{attempt}.meta.json", meta)
                if attempt == 5:
                    raise RuntimeError(f"GET failed: {endpoint}; see private metadata") from None
                time.sleep(min(2 ** attempt, 30))

    def day_pass(self, day, capture_id, pass_number):
        from erp_client import _parse_page_info
        rows, pages = [], []
        expected = None
        for page in range(1, 501):
            payload, meta, path = self.get("/ventas/", {"fechaDesde": day, "fechaHasta": day,
                "nroLote": page, "detallado": "true"}, self.root/"raw"/day/capture_id/str(pass_number)/str(page))
            if payload.get("error") or payload.get("Error"):
                raise ValueError("Chess application error; not an empty day")
            data = payload.get("dsReporteComprobantesApi", {}).get("VentasResumen")
            explicit_zero = bool(re.search(r"Cantidad de comprobantes totales:\s*0\s*$", str(payload.get("cantComprobantesVentas", ""))))
            if data is None and payload.get("dsReporteComprobantesApi") == {} and explicit_zero:
                data = []
            if not isinstance(data, list):
                raise ValueError("Invalid sales response envelope")
            current, total = _parse_page_info(payload.get("cantComprobantesVentas"))
            if expected is not None and total != expected:
                raise ValueError("Pagination changed during capture")
            if total is not None:
                expected = total
                if current not in (0, page):
                    raise ValueError("Unexpected reported page")
            if data and any(r.get("fechaComprobate") != day for r in data):
                raise ValueError("Row outside requested day")
            pages.append({**meta, "path": path, "page": page, "rows": len(data),
                          "pagination": payload.get("cantComprobantesVentas"), "explicit_zero": explicit_zero})
            rows.extend(data)
            if not data and page == 1:
                # An empty response stays distinguishable; certification uses independent evidence.
                break
            if total is None:
                raise ValueError("Missing pagination; completeness not certifiable")
            if page >= total:
                break
        else:
            raise ValueError("Pagination limit exceeded")
        return {"rows": len(rows), "digest": multiset_hash(rows), "pages": pages,
                "empty": not rows, "reported_pages": expected}

    def run(self, start, end):
        if (end-start).days != 59:
            raise ValueError("This runner is restricted to the approved 60-day pilot")
        preflight = json.loads((self.root/"preflight.json").read_text())
        if str(start) != preflight["start"] or str(end) != preflight["end"] or preflight["coverage"]["pilot"]["status"] != "complete":
            raise ValueError("Range must match the independently certified pilot")
        lock = (self.root/"capture.lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for i in range(60):
                day = str(start + timedelta(days=i))
                checkpoint = self.root/"days"/(day+".json")
                if checkpoint.exists() and json.loads(checkpoint.read_text()).get("status") == "source_verified":
                    continue
                entry = {"day": day, "capture_id": uuid4().hex, "started_at": now(), "status": "fetching", "passes": []}
                tick = time.monotonic()
                atomic_json(checkpoint, entry)
                try:
                    for pass_number in (1, 2):
                        entry["passes"].append(self.day_pass(day, entry["capture_id"], pass_number))
                        atomic_json(checkpoint, entry)
                    a,b = entry["passes"]
                    entry["status"] = "source_verified" if a["digest"] == b["digest"] and a["rows"] == b["rows"] else "source_changed"
                    entry["empty_certification"] = "explicit_source_zero_double_read_and_prior_ingestion" if a["empty"] and all(p.get("explicit_zero") for x in (a,b) for p in x["pages"]) else None
                    if a["empty"] and not entry["empty_certification"]:
                        entry["status"] = "empty_unconfirmed"
                except Exception as exc:
                    entry.update(status="failed", error_type=type(exc).__name__, error=str(exc))
                entry.update(finished_at=now(), seconds=time.monotonic()-tick)
                atomic_json(checkpoint, entry)
                atomic_json(self.root/"attempts"/(entry["capture_id"]+".json"), entry)
                print(canonical({"day":day,"status":entry["status"],"rows":entry["passes"][0]["rows"] if entry["passes"] else None,"seconds":round(entry["seconds"],2)}), flush=True)
        finally:
            lock.close()
