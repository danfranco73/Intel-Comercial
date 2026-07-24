#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mongo_client import get_db  # noqa: E402
from security.auth import normalize_email  # noqa: E402
from security.passwords import hash_password  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restablece de forma interactiva la contraseña de un usuario existente.",
    )
    parser.add_argument("email", help="Email del usuario a restablecer")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    email = normalize_email(args.email)
    db = get_db()
    if db is None:
        print("MongoDB no está configurado.", file=sys.stderr)
        return 1
    user = db["users"].find_one({"email": email})
    if user is None:
        print(f"No existe el usuario {email}.", file=sys.stderr)
        return 1

    password = getpass.getpass("Nueva contraseña: ")
    confirmation = getpass.getpass("Repetí la contraseña: ")
    if password != confirmation:
        print("Las contraseñas no coinciden.", file=sys.stderr)
        return 1
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    db["users"].update_one(
        {"_id": user["_id"]},
        {"$set": {"password_hash": password_hash, "updated_at": now}},
    )
    db["user_sessions"].update_many(
        {"user_id": user["_id"], "revoked_at": None},
        {"$set": {"revoked_at": now}},
    )
    db["login_attempts"].delete_many({"email": email})
    print(f"Contraseña restablecida para {email}. Las sesiones anteriores fueron revocadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
