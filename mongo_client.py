import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_NAME = "Intel-Comercial"
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
