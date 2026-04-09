from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_NAME = "Intel-Comercial"
_SESSION_ID = "default"   # sesión única por instalación
_client = None


def get_db():
    """Devuelve la base de datos Intel-Comercial. Retorna None si MONGO_URI no está configurado."""
    global _client
    uri = os.getenv("MONGO_URI")
    if not uri:
        return None
    if _client is None:
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return _client[DB_NAME]


def ping():
    """Comprueba la conexión con Atlas. Retorna True si está disponible."""
    db = get_db()
    if db is None:
        return False
    try:
        db.client.admin.command("ping")
        return True
    except ConnectionFailure:
        return False


def save_session(datasets: dict) -> bool:
    """
    Persiste la configuración de datasets (archivos, hojas, mappings, headerRow)
    en la colección 'sessions'. Hace upsert sobre el id fijo.

    Args:
        datasets: dict con la estructura que maneja el frontend (sales.sources, etc.)

    Returns:
        True si se guardó, False si Mongo no está disponible.
    """
    db = get_db()
    if db is None:
        return False
    try:
        db["sessions"].update_one(
            {"_id": _SESSION_ID},
            {"$set": {"datasets": datasets}},
            upsert=True,
        )
        return True
    except Exception:
        return False


def load_session() -> dict | None:
    """
    Recupera la configuración de datasets guardada.

    Returns:
        dict con key 'datasets', o None si no hay sesión guardada o Mongo no está disponible.
    """
    db = get_db()
    if db is None:
        return None
    try:
        doc = db["sessions"].find_one({"_id": _SESSION_ID}, {"_id": 0, "datasets": 1})
        return doc if doc else None
    except Exception:
        return None
