from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
from http.client import IncompleteRead
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

DEFAULT_LOGIN_PATH = "/auth/login"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 1000
DEFAULT_ARTICLE_PAGE_SIZE = 100
DEFAULT_BRANCH = 1
DEFAULT_ROUTE_SALES_FORCE = 1
DEFAULT_ROUTE_SALES_FORCES = "1,2,3,4"
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 0.75
DEFAULT_SESSION_TTL = 1200

_ERP_SESSION_LOCK = threading.Lock()
_ERP_SESSION_CACHE = {
    "cookie": None,
    "response": None,
    "expires_at": 0.0,
    "logged_at": 0.0,
}


class ERPError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def get_erp_config():
    return {
        "base_url": (os.getenv("CHESS_ERP_BASE_URL") or "").rstrip("/"),
        "login_path": os.getenv("ERP_LOGIN_PATH") or DEFAULT_LOGIN_PATH,
        "username": os.getenv("CHESS_ERP_USERNAME") or "",
        "password": os.getenv("CHESS_ERP_PASSWORD") or "",
        "timeout": float(os.getenv("CHESS_ERP_TIMEOUT") or DEFAULT_TIMEOUT),
        "verify_ssl": (os.getenv("CHESS_ERP_VERIFY_SSL") or "true").strip().lower() not in {"0", "false", "no"},
        "branch": _parse_int_env("CHESS_ERP_SUCURSAL", DEFAULT_BRANCH),
        "route_sales_force": _parse_int_env("CHESS_ERP_ROUTE_SALES_FORCE", DEFAULT_ROUTE_SALES_FORCE),
        "route_sales_forces": _parse_int_list_env("CHESS_ERP_ROUTE_SALES_FORCES", DEFAULT_ROUTE_SALES_FORCES),
        "retries": max(1, _parse_int_env("CHESS_ERP_RETRIES", DEFAULT_RETRIES)),
        "retry_delay": _parse_float_env("CHESS_ERP_RETRY_DELAY", DEFAULT_RETRY_DELAY),
        "session_ttl": max(60.0, _parse_float_env("CHESS_ERP_SESSION_TTL", DEFAULT_SESSION_TTL)),
    }


def erp_configured():
    config = get_erp_config()
    return bool(config["base_url"] and config["username"] and config["password"])


def get_erp_status():
    if not erp_configured():
        return {
            "configured": False,
            "reachable": False,
            "message": "Faltan variables de entorno para ChessERP.",
        }
    try:
        login = erp_login()
        return {
            "configured": True,
            "reachable": True,
            "message": "ChessERP autenticado correctamente.",
            "loginPath": get_erp_config()["login_path"],
            "hasSession": bool(login.get("cookie")),
            "sessionCached": _erp_session_valid(),
        }
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "message": f"No se pudo autenticar contra ChessERP: {exc}",
        }


def erp_login(force_refresh=False):
    if not force_refresh and _erp_session_valid():
        return _session_snapshot()

    with _ERP_SESSION_LOCK:
        if not force_refresh and _erp_session_valid():
            return _session_snapshot()
        session = _perform_erp_login()
        _ERP_SESSION_CACHE.update(session)
        return _session_snapshot()


def invalidate_erp_session(cookie=None):
    with _ERP_SESSION_LOCK:
        current_cookie = _ERP_SESSION_CACHE.get("cookie")
        if cookie and current_cookie and cookie != current_cookie:
            return
        _ERP_SESSION_CACHE.update({
            "cookie": None,
            "response": None,
            "expires_at": 0.0,
            "logged_at": 0.0,
        })


def _perform_erp_login():
    config = get_erp_config()
    if not erp_configured():
        raise ERPError("ChessERP no está configurado en .env")
    payload = {
        "usuario": config["username"],
        "password": config["password"],
    }
    response, headers = _request_json(
        f"{config['base_url']}{config['login_path']}",
        method="POST",
        payload=payload,
    )
    cookie = response.get("sessionId") or headers.get("Set-Cookie", "").split(";", 1)[0]
    if not cookie:
        raise ERPError("ChessERP no devolvió sessionId")
    now = time.monotonic()
    return {
        "cookie": cookie,
        "response": response,
        "logged_at": now,
        "expires_at": now + config["session_ttl"],
    }


def fetch_sales_dataset(fecha_desde, fecha_hasta, detailed=True, cookie=None):
    start_date = _parse_required_date(fecha_desde, "fechaDesde")
    end_date = _parse_required_date(fecha_hasta, "fechaHasta")
    if start_date > end_date:
        raise ERPError("fechaDesde no puede ser mayor que fechaHasta")

    raw_rows = []
    headers = []
    total_pages = None
    lot = 1

    while True:
        page = fetch_sales_page(start_date.isoformat(), end_date.isoformat(), lot, detailed=detailed, cookie=cookie)
        rows = ((page.get("dsReporteComprobantesApi") or {}).get("VentasResumen") or [])
        if rows and not headers:
            headers = list(rows[0].keys())
        raw_rows.extend(rows)

        page_count, reported_pages = _parse_page_info(page.get("cantComprobantesVentas"))
        if reported_pages:
            total_pages = reported_pages

        if total_pages is not None and lot >= total_pages:
            break
        if len(rows) < DEFAULT_PAGE_SIZE:
            break
        if not rows:
            break

        lot += 1
        if lot > 500:
            raise ERPError("ChessERP devolvió una paginación inesperada")

    detail_label = "detalladas" if detailed else "resumen"
    source_label = f"ChessERP ventas {detail_label} {start_date.isoformat()} a {end_date.isoformat()}"
    records = [record for record in (normalize_erp_sale_row(row) for row in raw_rows) if record]
    warning = None
    if not records:
        warning = _extract_sales_empty_reason(raw_rows, total_pages, page if 'page' in locals() else None)
    return {
        "datasetType": "sales",
        "sourceKind": "erp",
        "file": source_label,
        "sheet": "API ventas detalladas" if detailed else "API ventas",
        "headerRow": 0,
        "rowsRead": len(raw_rows),
        "rowsValid": len(records),
        "headers": headers,
        "mapping": {},
        "records": records,
        "warning": warning,
        "sourceCount": 1,
        "sources": [
            {
                "file": source_label,
                "sheet": "API ventas detalladas" if detailed else "API ventas",
                "headerRow": 0,
                "rowsRead": len(raw_rows),
                "rowsValid": len(records),
                "sourceKind": "erp",
            }
        ],
    }


def fetch_articles_dataset(cookie=None):
    raw_rows = []
    headers = []
    total_pages = None
    lot = 1

    while True:
        page = fetch_articles_page(lot, cookie=cookie)
        rows = ((page.get("Articulos") or {}).get("eArticulos") or [])
        if rows and not headers:
            headers = list(rows[0].keys())
        raw_rows.extend(rows)

        _, reported_pages = _parse_page_info(page.get("cantArticulos"))
        if reported_pages is not None:
            total_pages = reported_pages

        if total_pages is not None and lot >= total_pages:
            break
        if len(rows) < DEFAULT_ARTICLE_PAGE_SIZE:
            break
        if not rows:
            break

        lot += 1
        if lot > 500:
            raise ERPError("ChessERP devolvió una paginación inesperada para artículos")

    unique_records = {}
    for record in (normalize_erp_article_row(row) for row in raw_rows):
        if not record:
            continue
        unique_records[record["product_key"]] = record
    records = list(unique_records.values())
    if not records:
        raise ERPError("ChessERP no devolvió artículos válidos")

    return {
        "datasetType": "articles",
        "sourceKind": "erp",
        "file": "ChessERP artículos",
        "sheet": "API articulos",
        "headerRow": 0,
        "rowsRead": len(raw_rows),
        "rowsValid": len(records),
        "headers": headers,
        "mapping": {},
        "records": records,
    }


def fetch_staff_dataset(cookie=None):
    page = fetch_staff_page(get_erp_config()["branch"], cookie=cookie)
    rows = ((page.get("PersonalComercial") or {}).get("ePersCom") or (page.get("dsPersonalComercialApi") or {}).get("PersonalComercial") or [])
    headers = list(rows[0].keys()) if rows else []

    unique_records = {}
    for record in (normalize_erp_staff_row(row) for row in rows):
        if not record:
            continue
        key = record["seller_key"] or _normalize_lookup_key(record["seller_name"])
        current = unique_records.get(key)
        if _staff_priority(record) >= _staff_priority(current):
            unique_records[key] = record

    records = sorted(unique_records.values(), key=lambda item: (item.get("seller_name") or "", item.get("seller_key") or ""))
    if not records:
        raise ERPError("ChessERP no devolvió personal comercial válido")

    return {
        "datasetType": "sellers",
        "sourceKind": "erp",
        "file": "ChessERP personal comercial",
        "sheet": "API personalComercial",
        "headerRow": 0,
        "rowsRead": len(rows),
        "rowsValid": len(records),
        "headers": headers,
        "mapping": {},
        "records": records,
    }


def fetch_routes_dataset(cookie=None):
    config = get_erp_config()
    rows = []
    headers = list(rows[0].keys()) if rows else []
    for sales_force in config.get("route_sales_forces") or [config["route_sales_force"]]:
        page = fetch_routes_page(config["branch"], sales_force, cookie=cookie)
        page_rows = ((page.get("RutasVenta") or {}).get("eRutasVenta") or (page.get("RutaVenta") or {}).get("eRutas") or [])
        if page_rows and not headers:
            headers = list(page_rows[0].keys())
        rows.extend(page_rows)

    unique_records = {}
    for record in (normalize_erp_route_row(row) for row in rows):
        if not record:
            continue
        key = "|".join([record.get("branch_key") or "", record.get("sales_scheme_key") or "", record.get("route_key") or "", record.get("seller_key") or ""])
        current = unique_records.get(key)
        if _route_priority(record) >= _route_priority(current):
            record["client_keys"] = _merge_client_keys(current, record)
            unique_records[key] = record
        elif current:
            current["client_keys"] = _merge_client_keys(current, record)

    records = sorted(unique_records.values(), key=lambda item: (item.get("sales_force") or "", item.get("route_description") or "", item.get("seller_name") or ""))
    if not records:
        raise ERPError("ChessERP no devolvió rutas válidas")

    return {
        "datasetType": "routes",
        "sourceKind": "erp",
        "file": "ChessERP rutas de venta",
        "sheet": "API rutasVenta",
        "headerRow": 0,
        "rowsRead": len(rows),
        "rowsValid": len(records),
        "headers": headers,
        "mapping": {},
        "records": records,
    }


def fetch_marketing_dataset(cookie=None):
    page = fetch_marketing_page(cookie=cookie)
    segments = ((page.get("SubcanalesMkt") or {}).get("SegmentosMkt") or [])
    headers = list(segments[0].keys()) if segments else []

    records = []
    for segment in segments:
        channels = segment.get("CanalesMkt") or []
        if not channels:
            record = normalize_erp_marketing_row(segment=segment)
            if record:
                records.append(record)
            continue
        for channel in channels:
            subchannels = channel.get("SubCanalesMkt") or []
            if not subchannels:
                record = normalize_erp_marketing_row(segment=segment, channel=channel)
                if record:
                    records.append(record)
                continue
            for subchannel in subchannels:
                record = normalize_erp_marketing_row(segment=segment, channel=channel, subchannel=subchannel)
                if record:
                    records.append(record)

    unique_records = {}
    for record in records:
        unique_records[record["marketing_key"]] = record

    items = sorted(unique_records.values(), key=lambda item: (item.get("segment_name") or "", item.get("channel_name") or "", item.get("subchannel_name") or ""))
    if not items:
        raise ERPError("ChessERP no devolvió jerarquía de marketing válida")

    return {
        "datasetType": "marketing",
        "sourceKind": "erp",
        "file": "ChessERP jerarquía marketing",
        "sheet": "API jerarquiaMkt",
        "headerRow": 0,
        "rowsRead": len(records),
        "rowsValid": len(items),
        "headers": headers,
        "mapping": {},
        "records": items,
    }


def fetch_sales_page(fecha_desde, fecha_hasta, lote, detailed=True, cookie=None):
    config = get_erp_config()
    query = urlencode(
        {
            "fechaDesde": fecha_desde,
            "fechaHasta": fecha_hasta,
            "nroLote": lote,
            "detallado": "true" if detailed else "false",
        }
    )
    response, _ = _request_authenticated_json(
        f"{config['base_url']}/ventas/?{query}",
        cookie=cookie,
        headers={"Accept": "application/json"},
    )
    return response


def fetch_staff_page(branch, cookie=None):
    config = get_erp_config()
    response, _ = _request_authenticated_json(
        f"{config['base_url']}/personalComercial/?{urlencode({'sucursal': branch})}",
        cookie=cookie,
        headers={"Accept": "application/json"},
    )
    return response


def fetch_routes_page(branch, sales_force, cookie=None):
    config = get_erp_config()
    response, _ = _request_authenticated_json(
        f"{config['base_url']}/rutasVenta/?{urlencode({'sucursal': branch, 'fuerzaventa': sales_force, 'anulada': 'false'})}",
        cookie=cookie,
        headers={"Accept": "application/json"},
    )
    return response


def fetch_marketing_page(cookie=None):
    config = get_erp_config()
    response, _ = _request_authenticated_json(
        f"{config['base_url']}/jerarquiaMkt/?{urlencode({'CodScan': ''})}",
        cookie=cookie,
        headers={"Accept": "application/json"},
    )
    return response


def fetch_articles_page(lote, cookie=None):
    config = get_erp_config()
    response, _ = _request_authenticated_json(
        f"{config['base_url']}/articulos/?{urlencode({'nroLote': lote})}",
        cookie=cookie,
        headers={"Accept": "application/json"},
    )
    return response


def normalize_erp_sale_row(row):
    if _clean_text(row.get("anulado")) == "SI":
        return None

    date_value = _parse_optional_date(
        row.get("fechaComprobate")
        or row.get("fechaEntrega")
        or row.get("fechaAlta")
        or row.get("fechaPedido")
    )
    amount_final = _pick_number(row.get("subtotalFinal"), row.get("totalFinal"), row.get("importeFinal"))
    amount_net = _pick_number(row.get("subtotalNeto"), row.get("totalNeto"), row.get("importeNeto"))
    internal_taxes = _pick_number(
        row.get("subtotalImpuestosInternos"),
        row.get("impuestosInternos"),
        row.get("impuestoInterno"),
        row.get("impInterno"),
        row.get("impInternos"),
        0,
    ) or 0
    amount = _pick_number(
        amount_final,
        amount_net,
        row.get("subtotalBruto"),
    )
    client_key = _standard_key(row.get("idCliente"))
    if not date_value or amount is None or not client_key:
        return None

    seller_key = _standard_key(row.get("idVendedor"))
    seller_name = _clean_text(row.get("dsVendedor"))
    commercial_route = _clean_text(row.get("desRuta"))
    route_sheet = _clean_text(row.get("planillaCarga"))
    route_description = commercial_route or route_sheet
    product_key = _standard_key(row.get("idArticuloEstadistico")) or _standard_key(row.get("idArticulo"))
    product_name = _clean_text(row.get("dsArticuloEstadistico")) or _clean_text(row.get("dsArticulo"))
    segment_name = _clean_text(row.get("dsSegmentoMkt"))
    channel_name = _clean_text(row.get("dsCanalMkt"))
    subchannel_name = _clean_text(row.get("dsSubcanalMKT"))
    channel = (
        subchannel_name
        or channel_name
        or segment_name
        or _clean_text(row.get("dsNegocio"))
    )
    quantity = _pick_number(
        row.get("cantidadesTotal"),
        row.get("unimedtotal"),
        row.get("cantidadSolicitada"),
        row.get("unidadesSolicitadas"),
        0,
    ) or 0

    return {
        "row_version": row.get("rowVersion") or row.get("rowversion"),
        "document_key": _standard_key(row.get("idMovComercial")) or _standard_key(row.get("idDocumento")),
        "line_key": _standard_key(row.get("idLinea")),
        "detail_level": "line" if _standard_key(row.get("idLinea")) else "summary",
        "date": date_value,
        "year": date_value.year,
        "month": date_value.month,
        "client_key": client_key,
        "client_name": _clean_text(row.get("nombreCliente")) or client_key,
        "route_description": route_description,
        "commercial_route": commercial_route,
        "route_sheet": route_sheet,
        "seller_key": seller_key,
        "seller_name": seller_name,
        "sales_scheme_key": _standard_key(row.get("idFuerzaVentas")),
        "sales_scheme_name": _clean_text(row.get("dsFuerzaVentas")),
        "sales_force_key": _standard_key(row.get("idFuerzaVentas")),
        "sales_force": _clean_text(row.get("dsFuerzaVentas")),
        "product_key": product_key,
        "product_name": product_name,
        "segment_name": segment_name,
        "channel_name": channel_name,
        "subchannel_name": subchannel_name,
        "invoice": _build_invoice(row),
        "channel": channel,
        "amount": amount,
        "amount_net": amount_net if amount_net is not None else amount,
        "amount_final": amount_final if amount_final is not None else amount,
        "internal_taxes": internal_taxes,
        "amount_net_internal": (amount_net if amount_net is not None else amount) + internal_taxes,
        "quantity": quantity,
        "source": "ChessERP",
    }


def normalize_erp_article_row(row):
    if row.get("anulado") is True:
        return None

    product_key = _standard_key(row.get("idArticuloEstadistico")) or _standard_key(row.get("idArticulo"))
    if not product_key:
        return None

    groups = _grouping_map(row.get("eAgrupaciones") or [])
    return {
        "row_version": row.get("rowVersion") or row.get("rowversion"),
        "product_key": product_key,
        "product_name": _clean_text(row.get("desArticulo")) or product_key,
        "family": _pick_group(groups, ["FAMILIA"]),
        "line": _pick_group(groups, ["LINPRODU", "LINEA", "LINEAS DE PRODUCTO"]),
        "supplier": _pick_group(groups, ["ROTACION", "PROVEEDOR"]),
        "flavor": _pick_group(groups, ["SABOR", "SABORES"]),
        "brand": _pick_group(groups, ["MARCA", "MARCASAB"]),
        "business_unit": _pick_group(groups, ["UNIDAD DE NEGOCIO"]),
        "segment": _pick_group(groups, ["SEGMENTO"]),
        "division": _pick_group(groups, ["DIVISION"]),
        "generic": _pick_group(groups, ["GENERICO"]),
        "mark_flavor": _pick_group(groups, ["MARCASAB"]),
        "uxb": _clean_text(row.get("unidadesBulto")) or None,
        "caliber": _pick_group(groups, ["CALIBRE"]),
        "source": "ChessERP",
    }


def normalize_erp_staff_row(row):
    seller_key = _standard_key(row.get("idPersonal"))
    seller_name = _clean_text(row.get("desPersonal")) or _clean_text(row.get("apellido")) or seller_key
    if not seller_key and not seller_name:
        return None
    return {
        "row_version": row.get("rowVersion") or row.get("rowversion"),
        "seller_key": seller_key,
        "seller_name": seller_name,
        "sales_scheme_key": _standard_key(row.get("idFuerzaVentas")),
        "sales_scheme_name": _clean_text(row.get("desFuerzaVentas")),
        "sales_force_key": _standard_key(row.get("idFuerzaVentas")),
        "sales_force": _clean_text(row.get("desFuerzaVentas")),
        "branch_key": _standard_key(row.get("idSucursal")),
        "branch_name": _clean_text(row.get("desSucursal")),
        "role": _clean_text(row.get("cargo")),
        "sale_type": _clean_text(row.get("tipoVenta")),
        "supervisor_key": _standard_key(row.get("idPersonalSuperior")),
        "supervisor_name": _clean_text(row.get("desPersonalSuperior")),
        "source": "ChessERP",
    }


def normalize_erp_route_row(row):
    route_key = _standard_key(row.get("idRuta"))
    route_description = _clean_text(row.get("desRuta"))
    if not route_key and not route_description:
        return None
    valid_from = _parse_optional_date(row.get("fechaDesde"))
    valid_to = _parse_optional_date(row.get("fechaHasta"))
    today = date.today()
    is_active = bool(
        valid_from
        and valid_to
        and valid_from <= today <= valid_to
        and not bool(row.get("anulado"))
    )
    clients = row.get("eClientesRutas") or []
    client_keys = [
        key
        for key in (_standard_key(client.get("idCliente")) for client in clients)
        if key
    ]
    return {
        "route_key": route_key,
        "route_description": route_description or route_key,
        "seller_key": _standard_key(row.get("idPersonal")),
        "seller_name": _clean_text(row.get("desPersonal")),
        "sales_scheme_key": _standard_key(row.get("idFuerzaVentas")),
        "sales_scheme_name": _clean_text(row.get("desFuerzaVentas")),
        "sales_force_key": _standard_key(row.get("idFuerzaVentas")),
        "sales_force": _clean_text(row.get("desFuerzaVentas")),
        "branch_key": _standard_key(row.get("idSucursal")),
        "branch_name": _clean_text(row.get("desSucursal")),
        "mode": _clean_text(row.get("desModoAtencion")),
        "visit_days": _clean_text(row.get("diasVisita")),
        "delivery_days": _clean_text(row.get("diasEntrega")),
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_to": valid_to.isoformat() if valid_to else None,
        "client_keys": sorted(set(client_keys), key=_natural_key),
        "client_count": len(clients),
        "is_active": is_active,
        "source": "ChessERP",
    }


def normalize_erp_marketing_row(segment=None, channel=None, subchannel=None):
    segment_key = _standard_key((segment or {}).get("idSegmentoMkt"))
    channel_key = _standard_key((channel or {}).get("idCanalMkt"))
    subchannel_key = _standard_key((subchannel or {}).get("idSubcanalMkt"))
    segment_name = _clean_text((segment or {}).get("desSegmentoMkt"))
    channel_name = _clean_text((channel or {}).get("desCanalMkt"))
    subchannel_name = _clean_text((subchannel or {}).get("desSubcanalMkt"))
    marketing_key = subchannel_key or channel_key or segment_key
    if not marketing_key:
        return None
    return {
        "marketing_key": marketing_key,
        "segment_key": segment_key,
        "segment_name": segment_name,
        "channel_key": channel_key,
        "channel_name": channel_name,
        "subchannel_key": subchannel_key,
        "subchannel_name": subchannel_name,
        "source": "ChessERP",
    }


def _build_invoice(row):
    parts = [
        _clean_text(row.get("idDocumento")),
        _clean_text(row.get("letra")),
        _clean_text(row.get("serie")),
        _clean_text(row.get("nrodoc")),
    ]
    value = "-".join(part for part in parts if part)
    return value or _standard_key(row.get("idPedido")) or "ERP"


def _grouping_map(groups):
    mapping = {}
    for item in groups:
        key = _clean_text(item.get("idFormaAgrupar")) or _clean_text(item.get("desFormaAgrupar"))
        value = _clean_text(item.get("desAgrupacion"))
        if key and value:
            mapping[key.upper()] = value
    return mapping


def _pick_group(groups, aliases):
    for alias in aliases:
        value = groups.get(alias.upper())
        if value:
            return value
    return None


def _staff_priority(record):
    if not record:
        return (-1, -1, -1, "")
    return (
        1 if record.get("role") == "VENDEDOR" else 0,
        1 if record.get("sales_force") else 0,
        1 if record.get("row_version") else 0,
        record.get("seller_name") or "",
    )


def _route_priority(record):
    if not record:
        return (-1, "", "", -1)
    return (
        1 if record.get("is_active") else 0,
        record.get("valid_to") or "",
        record.get("valid_from") or "",
        record.get("client_count") or 0,
    )


def _merge_client_keys(*records):
    keys = set()
    for record in records:
        for key in (record or {}).get("client_keys") or []:
            if key:
                keys.add(str(key))
    return sorted(keys, key=_natural_key)


def _natural_key(value):
    text = str(value)
    return (0, int(text)) if text.isdigit() else (1, text)


def _request_json(url, method="GET", payload=None, headers=None):
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=data, method=method)
    for key, value in request_headers.items():
        request.add_header(key, value)

    config = get_erp_config()
    context = None if config["verify_ssl"] else ssl._create_unverified_context()
    retries = max(1, config["retries"])
    retry_delay = max(0.1, config["retry_delay"])
    retryable_http_codes = {408, 409, 425, 429, 500, 502, 503, 504}

    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=config["timeout"], context=context) as response:
                body = _read_body(response)
                return json.loads(body), dict(response.headers.items())
        except HTTPError as exc:
            detail = _read_body(exc)[:300] or str(exc)
            if exc.code in retryable_http_codes and attempt < retries:
                time.sleep(retry_delay * attempt)
                continue
            raise ERPError(f"HTTP {exc.code} en ChessERP: {detail}", status_code=exc.code) from exc
        except (URLError, IncompleteRead) as exc:
            if attempt < retries:
                time.sleep(retry_delay * attempt)
                continue
            reason = getattr(exc, "reason", None) or str(exc)
            raise ERPError(f"No se pudo conectar con ChessERP: {reason}") from exc
        except json.JSONDecodeError as exc:
            if attempt < retries:
                time.sleep(retry_delay * attempt)
                continue
            raise ERPError("ChessERP devolvió una respuesta no JSON") from exc


def _request_authenticated_json(url, method="GET", payload=None, headers=None, cookie=None):
    active_cookie = cookie or erp_login()["cookie"]
    request_headers = dict(headers or {})
    request_headers["Cookie"] = active_cookie
    try:
        response, response_headers = _request_json(url, method=method, payload=payload, headers=request_headers)
        _touch_erp_session(active_cookie)
        return response, response_headers
    except ERPError as exc:
        if exc.status_code not in {401, 403}:
            raise
        invalidate_erp_session(active_cookie)
        refreshed_cookie = erp_login(force_refresh=True)["cookie"]
        request_headers["Cookie"] = refreshed_cookie
        response, response_headers = _request_json(url, method=method, payload=payload, headers=request_headers)
        _touch_erp_session(refreshed_cookie)
        return response, response_headers


def _parse_page_info(text):
    if not text:
        return None, None
    match = re.search(r"(\d+)\s*/\s*(\d+)", str(text))
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _extract_sales_empty_reason(raw_rows, total_pages, page):
    if raw_rows:
        return "ChessERP devolvió comprobantes pero ninguna línea válida para el modelo interno."
    payload = page or {}
    for item in payload.get("error") or []:
        message = _clean_text(item.get("mensaje"))
        if message:
            return message
    page_info = _clean_text(payload.get("cantComprobantesVentas"))
    if "Cantidad de comprobantes: 0" in page_info:
        return "ChessERP no devolvió ventas para el rango seleccionado."
    if total_pages == 0:
        return "ChessERP no generó lotes para el rango seleccionado."
    return "ChessERP no devolvió ventas para el rango seleccionado."


def _parse_required_date(value, field_name):
    parsed = _parse_optional_date(value)
    if not parsed:
        raise ERPError(f"{field_name} debe tener formato YYYY-MM-DD")
    return parsed


def _parse_optional_date(value):
    text = _clean_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _pick_number(*values):
    for value in values:
        parsed = _parse_number(value)
        if parsed is not None:
            return parsed
    return None


def _parse_number(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _standard_key(value):
    text = _clean_text(value)
    if not text or text == "0":
        return None
    number = _parse_number(text)
    if number is not None and float(number).is_integer():
        return str(int(number))
    return text


def _parse_int_env(name, default_value):
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default_value
    try:
        return int(str(value).strip())
    except ValueError:
        return default_value


def _parse_int_list_env(name, default_value):
    raw = os.getenv(name) or default_value
    values = []
    for item in str(raw).replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError:
            continue
    return values or [DEFAULT_ROUTE_SALES_FORCE]


def _parse_float_env(name, default_value):
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default_value
    try:
        return float(str(value).strip())
    except ValueError:
        return default_value


def _session_snapshot():
    return {
        "cookie": _ERP_SESSION_CACHE.get("cookie"),
        "response": _ERP_SESSION_CACHE.get("response"),
        "logged_at": _ERP_SESSION_CACHE.get("logged_at"),
        "expires_at": _ERP_SESSION_CACHE.get("expires_at"),
    }


def _erp_session_valid():
    cookie = _ERP_SESSION_CACHE.get("cookie")
    expires_at = _ERP_SESSION_CACHE.get("expires_at") or 0.0
    return bool(cookie and time.monotonic() < expires_at)


def _touch_erp_session(cookie):
    with _ERP_SESSION_LOCK:
        if cookie and _ERP_SESSION_CACHE.get("cookie") == cookie:
            _ERP_SESSION_CACHE["expires_at"] = time.monotonic() + get_erp_config()["session_ttl"]


def _normalize_lookup_key(value):
    return _clean_text(value).lower()


def _clean_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if not text else text


def _read_body(response):
    try:
        raw = response.read()
    except IncompleteRead as exc:
        raw = exc.partial
    return raw.decode("utf-8", "replace")
