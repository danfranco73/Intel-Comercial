from __future__ import annotations

from contextlib import contextmanager
from threading import BoundedSemaphore

from sales_coach.config import int_env


class AnalysisCapacityExceeded(RuntimeError):
    """No se liberó un turno de análisis dentro del tiempo de espera."""


# Cada análisis carga cientos de miles de filas (~1 GB por mes analizado), así que
# se limita cuántos corren a la vez para que varios usuarios simultáneos no agoten
# la memoria de la instancia; los demás esperan su turno.
MAX_CONCURRENT_ANALYSES = int_env("APP_MAX_CONCURRENT_ANALYSES", 1, minimum=1)
ANALYSIS_QUEUE_TIMEOUT_SECONDS = int_env("APP_ANALYSIS_QUEUE_TIMEOUT_SECONDS", 120, minimum=1)

_analysis_slots = BoundedSemaphore(MAX_CONCURRENT_ANALYSES)


@contextmanager
def heavy_analysis_slot(timeout: float | None = None):
    wait = ANALYSIS_QUEUE_TIMEOUT_SECONDS if timeout is None else timeout
    if not _analysis_slots.acquire(timeout=wait):
        raise AnalysisCapacityExceeded(
            "Hay demasiados análisis en curso. Probá de nuevo en unos minutos."
        )
    try:
        yield
    finally:
        _analysis_slots.release()
