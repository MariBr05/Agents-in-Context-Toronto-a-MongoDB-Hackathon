"""Embedding providers. All return {"vector", "model", "dimensions"}.

OpenAIEmbedder is the real provider (stdlib HTTP, no extra dependency).
HashTestEmbedder is a deterministic local stand-in for UNIT TESTS ONLY: its model
name is "test-local-hash" so it can never be mistaken for a real embedding.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict

from .models import ChangeRequest


class EmbeddingError(Exception):
    pass


class OpenAIEmbedder:
    URL = "https://api.openai.com/v1/embeddings"

    def __init__(self, api_key: str, model: str = "text-embedding-3-small", dimensions: int = 1536,
                 timeout: float = 30.0):
        if not api_key:
            raise EmbeddingError("OPENAI_API_KEY is not set (put it in your local .env).")
        self._key, self.model, self.dimensions, self.timeout = api_key, model, dimensions, timeout

    def __call__(self, text: str) -> Dict[str, Any]:
        body = json.dumps({"model": self.model, "input": text, "dimensions": self.dimensions}).encode()
        req = urllib.request.Request(self.URL, data=body, method="POST", headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
            vec = data["data"][0]["embedding"]
        except urllib.error.HTTPError as e:  # never include headers/key in the message
            raise EmbeddingError("OpenAI embeddings request failed: HTTP %d" % e.code)
        except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError) as e:
            raise EmbeddingError("OpenAI embeddings request failed: %s" % type(e).__name__)
        if len(vec) != self.dimensions:
            raise EmbeddingError("Provider returned %d dimensions, expected %d" % (len(vec), self.dimensions))
        return {"vector": vec, "model": self.model, "dimensions": self.dimensions}


class HashTestEmbedder:
    """TEST ONLY. Hashed bag-of-words, L2-normalised. Not semantic; not for Atlas demos."""
    model = "test-local-hash"

    def __init__(self, dimensions: int = 64):
        self.dimensions = dimensions

    def __call__(self, text: str) -> Dict[str, Any]:
        v = [0.0] * self.dimensions
        for tok in re.findall(r"[a-z0-9_]+", text.lower()):
            v[int(hashlib.sha256(tok.encode()).hexdigest(), 16) % self.dimensions] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return {"vector": [x / n for x in v], "model": self.model, "dimensions": self.dimensions}


def get_embedder():
    """Build the configured real embedder from the environment."""
    provider = os.environ.get("EMBEDDING_PROVIDER", "openai").lower()
    if provider != "openai":
        raise EmbeddingError("Unsupported EMBEDDING_PROVIDER %r (only 'openai' for live mode)" % provider)
    return OpenAIEmbedder(os.environ.get("OPENAI_API_KEY", ""),
                          os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
                          int(os.environ.get("EMBEDDING_DIMENSIONS", "1536")))


def ticket_query_text(ticket: ChangeRequest) -> str:
    """Change-request/service context embedded at query time (source facts only)."""
    return "\n".join([
        "service: " + ticket.service_id,
        "change type: " + ticket.change_type,
        "environment: " + ticket.target_environment,
        "change: " + ticket.proposed_change,
        "dependencies: " + ", ".join(ticket.known_dependencies),
    ])
