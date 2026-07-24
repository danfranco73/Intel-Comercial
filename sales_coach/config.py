from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    base_dir: Path
    parent_dir: Path
    static_dir: Path
    vendor_dir: Path
    upload_dir: Path
    log_dir: Path
    error_log: Path
    host: str
    port: int
    max_scan_depth: int
    allowed_extensions: frozenset[str]

    @property
    def allowed_roots(self) -> tuple[Path, Path]:
        return self.base_dir, self.parent_dir


def load_app_config() -> AppConfig:
    base_dir = Path(__file__).resolve().parents[1]
    static_dir = base_dir / "static"
    log_dir = base_dir / "logs"
    return AppConfig(
        base_dir=base_dir,
        parent_dir=base_dir.parent,
        static_dir=static_dir,
        vendor_dir=static_dir / "vendor",
        upload_dir=base_dir / "uploads",
        log_dir=log_dir,
        error_log=log_dir / "app_errors.log",
        host=(os.getenv("APP_HOST") or "127.0.0.1").strip(),
        port=int_env("APP_PORT", 8765, minimum=1),
        max_scan_depth=int_env("APP_MAX_SCAN_DEPTH", 2, minimum=0),
        allowed_extensions=frozenset({".xlsx", ".xlsm"}),
    )


def int_env(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value) if minimum is not None else value


def erp_sales_chunk_days() -> int:
    return int_env("CHESS_ERP_SALES_CHUNK_DAYS", 31, minimum=1)


def max_json_body_bytes() -> int:
    return int_env("APP_MAX_JSON_BODY_BYTES", 512 * 1024, minimum=1024)


def max_upload_body_bytes() -> int:
    return int_env("APP_MAX_UPLOAD_BODY_BYTES", 25 * 1024 * 1024, minimum=1024 * 1024)


def admin_rate_limit_window_seconds() -> int:
    return int_env("APP_ADMIN_RATE_LIMIT_WINDOW_SECONDS", 60, minimum=1)


def admin_rate_limit_max_requests() -> int:
    return int_env("APP_ADMIN_RATE_LIMIT_MAX_REQUESTS", 20, minimum=1)
