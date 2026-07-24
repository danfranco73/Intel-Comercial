#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mongo_client import get_db  # noqa: E402
from security.auth import bootstrap_admin, ensure_auth_indexes  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crea índices de autenticación y, opcionalmente, el admin inicial.",
    )
    parser.add_argument(
        "--indexes-only",
        action="store_true",
        help="Crea únicamente colecciones/índices, sin usuario inicial.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db = get_db()
    if db is None:
        raise SystemExit("MongoDB no está configurado.")

    ensure_auth_indexes(db)
    print("Índices de users, user_sessions y login_attempts verificados.")
    if args.indexes_only:
        return 0

    email = (os.getenv("BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or ""
    name = (os.getenv("BOOTSTRAP_ADMIN_NAME") or "Administrador").strip()
    if not email or not password:
        raise SystemExit(
            "Definí BOOTSTRAP_ADMIN_EMAIL y BOOTSTRAP_ADMIN_PASSWORD, "
            "o usá --indexes-only. No existe contraseña predeterminada."
        )
    created = bootstrap_admin(db, email, password, name)
    print(f"Administrador creado: {created['email']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
