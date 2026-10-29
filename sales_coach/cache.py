from __future__ import annotations

import time
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
    status_cache.clear()
