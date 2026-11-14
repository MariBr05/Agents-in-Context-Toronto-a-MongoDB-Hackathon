"""Environment configuration. Never prints or logs the Mongo URI / API keys."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    mongodb_db: str
    vector_index: str
    embedding_dimensions: int


def load_settings() -> Settings:
    load_dotenv()
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri or "<username>" in uri or "<cluster-host>" in uri:
        raise ConfigError("MONGODB_URI is not set. Copy .env.example to .env and fill it in (never commit .env).")
    return Settings(
        mongodb_uri=uri,
        mongodb_db=os.environ.get("MONGODB_DB", "reviewloop_demo"),
        vector_index=os.environ.get("MONGODB_VECTOR_INDEX", "memory_vector_index"),
        embedding_dimensions=int(os.environ.get("EMBEDDING_DIMENSIONS", "1536")),
    )


def connect(settings: Settings):
    from pymongo import MongoClient
    from pymongo.server_api import ServerApi
    return MongoClient(settings.mongodb_uri, server_api=ServerApi("1"), serverSelectionTimeoutMS=8000)[settings.mongodb_db]
