from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from contextlib import contextmanager
from threading import Lock
from typing import Any, Callable, Hashable

from sales_coach.config import int_env


# Los estados de las bases (conteos de Mongo, partes de ClickHouse) y las
# opciones de prefiltro solo cambian con una sync, pero se pedían en cada carga
# de página de cada usuario. Se guardan unos minutos y se limpian al sincronizar;
# una sync programada en otro proceso se ve, a lo sumo, al vencer el TTL.
STATUS_CACHE_TTL_SECONDS = int_env("APP_STATUS_CACHE_TTL_SECONDS", 120, minimum=0)


class TtlCache:
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._lock = Lock()

    def get_or_compute(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                return entry[1]
        value = compute()
        if self.ttl_seconds > 0:
            with self._lock:
                self._entries[key] = (time.monotonic() + self.ttl_seconds, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


status_cache = TtlCache(STATUS_CACHE_TTL_SECONDS)


def cached_status(key: Hashable, compute: Callable[[], Any]) -> Any:
    return status_cache.get_or_compute(key, compute)


def invalidate_status_cache() -> None:
    """Se llama al terminar una sync: los estados y los informes quedan viejos."""
    status_cache.clear()
    result_cache.clear()


# Informes calculados (/api/analyze, /api/bi/tactical-month). Calcular uno lee
# cientos de miles de filas; si otro usuario pide el mismo período y filtros con
# los mismos datos, se reutiliza. La clave incluye las fechas de última sync, así
# que una sync programada en otro proceso invalida en cuanto vence el caché de
# estados. Se guarda el JSON ya comprimido para acotar la memoria.
RESULT_CACHE_TTL_SECONDS = int_env("APP_RESULT_CACHE_TTL_SECONDS", 900, minimum=0)
RESULT_CACHE_MAX_BYTES = int_env("APP_RESULT_CACHE_MAX_MB", 64, minimum=0) * 1024 * 1024


class ResultCache:
    def __init__(self, ttl_seconds: float, max_bytes: int):
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()
        self._key_locks: dict[str, Lock] = {}

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0 and self.max_bytes > 0

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry["expires"] <= time.monotonic():
                self._drop(key)
                return None
            self._entries.move_to_end(key)
            return entry

    def put(self, key: str, body_gzip: bytes, extra: dict[str, Any] | None = None) -> None:
        if not self.enabled or len(body_gzip) > self.max_bytes:
            return
        with self._lock:
            if key in self._entries:
                self._drop(key)
            self._entries[key] = {
                "expires": time.monotonic() + self.ttl_seconds,
                "body_gzip": body_gzip,
                "extra": extra or {},
            }
            self._bytes += len(body_gzip)
            while self._bytes > self.max_bytes and self._entries:
                self._drop(next(iter(self._entries)))

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry["body_gzip"])

    @contextmanager
    def single_flight(self, key: str):
        """Un solo cálculo por clave: quien llega segundo espera y lee el caché."""
        with self._lock:
            key_lock = self._key_locks.setdefault(key, Lock())
        with key_lock:
            yield
        with self._lock:
            if self._key_locks.get(key) is key_lock and not key_lock.locked():
                self._key_locks.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "bytes": self._bytes}


result_cache = ResultCache(RESULT_CACHE_TTL_SECONDS, RESULT_CACHE_MAX_BYTES)


def result_cache_key(kind: str, payload: Any, scope_filters: Any, data_version: Any) -> str:
    material = json.dumps(
        {"kind": kind, "payload": payload, "scope": scope_filters, "version": data_version},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def data_version(*statuses: dict[str, Any]) -> list[Any]:
    """Fechas de última sync (ventas y maestros) de cada base: cambian con cada sync."""
    return [
        sorted((key, value) for key, value in (status or {}).items() if key.lower().endswith("lastsyncat"))
        for status in statuses
    ]
