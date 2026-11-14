"""Atlas Vector Search: index management and scoped $vectorSearch retrieval.

Scope (team/service/status/approval/latest) is enforced INSIDE the $vectorSearch
filter, then every hit is re-validated in app code (team, service, status, expiry,
embedding model/dimensions, supported rule_type). Similarity never decides
applicability.

Known limitation: `expires_at` is not in the pre-filter (null-or-future cannot be
expressed there), so an expired memory can occupy one of the `limit` slots before
being dropped by the post-check. We over-fetch (limit=20) and callers should run
Repository.expire_due() beforehand to flip expired memories out of `status=active`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from .models import ChangeRequest
from .repository import MEMORY, Repository
from .rules import VALIDATORS

MODE_ATLAS = "atlas_vector"
MODE_STUB = "test_stub"
NUM_CANDIDATES = 100
LIMIT = 20


class VectorSearchError(Exception):
    pass


def index_definition(dimensions: int) -> Dict[str, Any]:
    return {"fields": [
        {"type": "vector", "path": "embedding", "numDimensions": dimensions, "similarity": "cosine"},
        {"type": "filter", "path": "team_id"},
        {"type": "filter", "path": "service_id"},
        {"type": "filter", "path": "status"},
        {"type": "filter", "path": "approval_status"},
        {"type": "filter", "path": "is_latest"},
        {"type": "filter", "path": "expires_at"},
    ]}


def _find_index(db, name):
    try:
        for ix in db[MEMORY].list_search_indexes(name):
            if ix.get("name") == name:
                return ix
    except PyMongoError as e:
        raise VectorSearchError("Could not list Atlas search indexes (is this an Atlas cluster with Search "
                                "enabled?): %s" % type(e).__name__)
    return None


def _dims_of(ix) -> Any:
    for f in (ix.get("latestDefinition") or ix.get("definition") or {}).get("fields", []):
        if f.get("type") == "vector":
            return f.get("numDimensions")
    return None


def index_status(db, name: str, dimensions: int) -> Dict[str, Any]:
    """Raise VectorSearchError with an actionable message unless the index exists, matches dims, and is queryable."""
    ix = _find_index(db, name)
    if ix is None:
        raise VectorSearchError("Vector index %r does not exist. Run: python -m reviewloop.cli setup-indexes" % name)
    if _dims_of(ix) != dimensions:
        raise VectorSearchError("Vector index %r has numDimensions=%s but EMBEDDING_DIMENSIONS=%d. Drop and "
                                "recreate the index, or fix the env var." % (name, _dims_of(ix), dimensions))
    if not ix.get("queryable"):
        raise VectorSearchError("Vector index %r exists but is not queryable yet (status=%s). Wait and retry."
                                % (name, ix.get("status")))
    return {"name": name, "queryable": True, "dimensions": dimensions}


def ensure_search_index(db, name: str, dimensions: int, wait_seconds: float = 180.0, poll: float = 3.0):
    """Create the BYO-vector index if absent, then wait until queryable. Idempotent."""
    ix = _find_index(db, name)
    if ix is None:
        try:
            db[MEMORY].create_search_index(SearchIndexModel(
                definition=index_definition(dimensions), name=name, type="vectorSearch"))
        except PyMongoError as e:
            raise VectorSearchError("Creating vector index failed (%s). Check the cluster tier supports Vector "
                                    "Search and the DB user may create search indexes." % type(e).__name__)
    deadline = time.time() + wait_seconds
    while True:
        ix = _find_index(db, name)
        if ix is not None and _dims_of(ix) != dimensions:
            raise VectorSearchError("Existing index %r has numDimensions=%s, expected %d."
                                    % (name, _dims_of(ix), dimensions))
        if ix is not None and ix.get("queryable"):
            return ix
        if time.time() > deadline:
            raise VectorSearchError("Index %r not queryable after %ds; check Atlas UI." % (name, int(wait_seconds)))
        time.sleep(poll)


def build_pipeline(query_vector: List[float], team_id: str, service_id: str, index: str,
                   limit: int = LIMIT, num_candidates: int = NUM_CANDIDATES) -> List[Dict[str, Any]]:
    return [
        {"$vectorSearch": {
            "index": index, "path": "embedding", "queryVector": query_vector,
            "numCandidates": num_candidates, "limit": limit,
            "filter": {"$and": [
                {"team_id": team_id}, {"service_id": service_id},
                {"status": "active"}, {"approval_status": "approved"}, {"is_latest": True}]}}},
        {"$project": {
            "embedding": 0, "_id": 0,
            "memory_id": 1, "revision": 1, "rule_type": 1, "rule_text": 1, "required_action": 1,
            "source_ticket": 1, "team_id": 1, "service_id": 1, "status": 1, "approval_status": 1,
            "is_latest": 1, "expires_at": 1, "embedding_model": 1, "embedding_dimensions": 1,
            "score": {"$meta": "vectorSearchScore"}}},
    ]


@dataclass
class Retrieval:
    mode: str
    candidates: int = 0
    eligible: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)  # [{"ref": "MEM-001@1", "reason": "..."}]

    @property
    def refs(self) -> List[str]:
        return ["%s@%d" % (m["memory_id"], m["revision"]) for m in self.eligible]


def _post_filter(repo: Repository, hits, team_id, service_id, model, dims) -> Retrieval:
    res = Retrieval(mode=MODE_ATLAS, candidates=len(hits))
    for h in hits:
        ref = "%s@%s" % (h.get("memory_id"), h.get("revision"))
        if not repo.is_eligible(h, team_id, service_id):
            reason = "outside scope/inactive/expired"
        elif h.get("embedding_model") != model or h.get("embedding_dimensions") != dims:
            reason = "embedding model/dimensions mismatch"
        elif h.get("rule_type") not in VALIDATORS:
            reason = "unsupported rule_type"
        else:
            res.eligible.append(h)
            continue
        res.rejected.append({"ref": ref, "reason": reason})
    return res


def search_memories(repo: Repository, embedder, ticket: ChangeRequest, team_id: str,
                    index: str = "memory_vector_index", query_text: str = None) -> Retrieval:
    """Live Atlas retrieval. Raises VectorSearchError/EmbeddingError; never silently falls back."""
    from .embeddings import ticket_query_text
    q = embedder(query_text or ticket_query_text(ticket))
    index_status(repo.db, index, q["dimensions"])
    pipeline = build_pipeline(q["vector"], team_id, ticket.service_id, index)
    try:
        hits = list(repo.db[MEMORY].aggregate(pipeline))
    except OperationFailure as e:
        raise VectorSearchError("$vectorSearch failed: %s" % (e.details.get("errmsg") if e.details else e))
    except PyMongoError as e:
        raise VectorSearchError("Atlas query failed: %s" % type(e).__name__)
    return _post_filter(repo, hits, team_id, ticket.service_id, q["model"], q["dimensions"])


def confirm_indexed(repo: Repository, embedder, ticket: ChangeRequest, team_id: str, memory_ref: str,
                    index: str = "memory_vector_index", wait_seconds: float = 60.0, poll: float = 2.0) -> Retrieval:
    """Poll until Atlas Vector Search returns `memory_ref` (e.g. 'MEM-001@1') for this scope.
    Atlas indexes asynchronously, so a fresh insert may take seconds to become searchable."""
    deadline = time.time() + wait_seconds
    while True:
        r = search_memories(repo, embedder, ticket, team_id, index)
        if memory_ref in r.refs:
            return r
        if time.time() > deadline:
            raise VectorSearchError("%s was not returned by Vector Search within %ds" % (memory_ref, int(wait_seconds)))
        time.sleep(poll)


class StubRetriever:
    """EXPLICIT test stub: metadata-only eligibility, no vectors. Mode is 'test_stub'; never claim Atlas."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def search(self, ticket: ChangeRequest, team_id: str) -> Retrieval:
        elig = [m for m in self.repo.eligible_memories(team_id, ticket.service_id) if m["rule_type"] in VALIDATORS]
        return Retrieval(mode=MODE_STUB, candidates=len(elig), eligible=elig)
