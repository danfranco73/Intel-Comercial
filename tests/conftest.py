from __future__ import annotations

import json
import ipaddress
import os
import socket
from datetime import date
from pathlib import Path

import dotenv
import pytest


# Test collection must never load a developer's production connectors or secrets.
# Individual tests may still set synthetic configuration with monkeypatch.
dotenv.load_dotenv = lambda *args, **kwargs: False
for _key in list(os.environ):
    if _key.startswith(("MONGO", "CLICKHOUSE", "CHESS_ERP", "BOOTSTRAP_", "TEMANDO", "TMA_")):
        os.environ.pop(_key)

_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex
_socket_getaddrinfo = socket.getaddrinfo
_socket_sendto = socket.socket.sendto


def _local_host(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _test_connect(sock, address):
    if sock.family != socket.AF_UNIX and not _local_host(address[0]):
        raise RuntimeError("Tests prohibit external connections")
    return _socket_connect(sock, address)


def _test_connect_ex(sock, address):
    if sock.family != socket.AF_UNIX and not _local_host(address[0]):
        raise RuntimeError("Tests prohibit external connections")
    return _socket_connect_ex(sock, address)


def _test_getaddrinfo(host, *args, **kwargs):
    if host is not None and not _local_host(host):
        raise RuntimeError("Tests prohibit external DNS resolution")
    return _socket_getaddrinfo(host, *args, **kwargs)


def _test_sendto(sock, data, *args):
    if sock.family != socket.AF_UNIX and not _local_host(args[-1][0]):
        raise RuntimeError("Tests prohibit external datagrams")
    return _socket_sendto(sock, data, *args)


socket.socket.connect = _test_connect
socket.socket.connect_ex = _test_connect_ex
socket.getaddrinfo = _test_getaddrinfo
socket.socket.sendto = _test_sendto


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def sales_records():
    records = load_fixture("sales.json")
    for record in records:
        record["date"] = date.fromisoformat(record["date"])
    return records


@pytest.fixture
def datasets(sales_records):
    return {
        "sales": {
            "datasetType": "sales",
            "sourceKind": "fixture",
            "file": "ventas_anonimas.json",
            "sheet": "Ventas",
            "headerRow": 0,
            "rowsRead": len(sales_records),
            "rowsValid": len(sales_records),
            "records": sales_records,
            "analysisRange": {"fechaDesde": "2026-06-01", "fechaHasta": "2026-06-30"},
            "comparisonRange": {"fechaDesde": "2026-05-01", "fechaHasta": "2026-05-31"},
        },
        "articles": {
            "datasetType": "articles",
            "file": "articulos_anonimos.json",
            "sheet": "Artículos",
            "records": load_fixture("articles.json"),
        },
        "sellers": {
            "datasetType": "sellers",
            "file": "vendedores_anonimos.json",
            "sheet": "Vendedores",
            "records": load_fixture("sellers.json"),
        },
        "routes": {
            "datasetType": "routes",
            "file": "rutas_anonimas.json",
            "sheet": "Rutas",
            "records": load_fixture("routes.json"),
        },
    }
