"""Vector-search logic tests with a FAKE $vectorSearch (mongomock can't run it).
These verify pipeline construction, post-filtering, index checks and error handling.
They do NOT prove Atlas integration; see tests/test_atlas_integration.py."""
import io
import json
from datetime import timedelta

import mongomock
import pytest
from pymongo.errors import OperationFailure

from reviewloop import vector_search as vs
from reviewloop.embeddings import EmbeddingError, HashTestEmbedder, OpenAIEmbedder, ticket_query_text
from reviewloop.identity import resolve_actor
from reviewloop.plan_hash import load_ticket
from reviewloop.repository import Repository, utcnow
from reviewloop.rules import RULE_DASHBOARD_REFRESH
from pathlib import Path

TICKET = load_ticket(Path(__file__).resolve().parent.parent / "fixtures" / "mongodb_aggregation_change.json")
SVC = TICKET.service_id
EMB = HashTestEmbedder(64)


class FakeColl:
    def __init__(self, real, owner):
        self._real, self._o = real, owner

    def __getattr__(self, n):
        return getattr(self._real, n)

    def list_search_indexes(self, name=None):
        if isinstance(self._o.indexes, Exception):
            raise self._o.indexes
        return iter(self._o.indexes)

    def aggregate(self, pipeline):
        self._o.pipelines.append(pipeline)
        if isinstance(self._o.hits, Exception):
            raise self._o.hits
        return iter(self._o.hits)

    def create_search_index(self, model):
        self._o.created.append(model)
        self._o.indexes = [{"name": model.document["name"], "queryable": True,
                            "latestDefinition": model.document["definition"]}]


class FakeDB:
    def __init__(self, dims=64):
        self.real = mongomock.MongoClient().vtest
        self.indexes = [{"name": "memory_vector_index", "queryable": True,
                         "latestDefinition": vs.index_definition(dims)}]
        self.hits, self.pipelines, self.created = [], [], []

    def __getitem__(self, n):
        return FakeColl(self.real[n], self) if n == "memory_revisions" else self.real[n]

    def command(self, *a, **k):
        return {"ok": 1}

    def list_collection_names(self):
        return self.real.list_collection_names()


@pytest.fixture
def fdb():
    return FakeDB()


@pytest.fixture
def repo(fdb):
    return Repository(fdb, embedder=EMB)


def hit(**over):
    d = {"memory_id": "MEM-001", "revision": 1, "team_id": "team-alpha", "service_id": SVC, "status": "active",
         "approval_status": "approved", "is_latest": True, "expires_at": None, "rule_type": RULE_DASHBOARD_REFRESH,
         "embedding_model": "test-local-hash", "embedding_dimensions": 64, "score": 0.93,
         "source_ticket": "INC-MDB-017"}
    d.update(over)
    return d


def test_pipeline_filters_scope_inside_vector_stage():
    p = vs.build_pipeline([0.1], "team-alpha", SVC, "memory_vector_index")
    stage = p[0]["$vectorSearch"]
    assert stage["index"] == "memory_vector_index" and stage["path"] == "embedding"
    assert {"team_id": "team-alpha"} in stage["filter"]["$and"]
    assert {"service_id": SVC} in stage["filter"]["$and"]
    assert {"status": "active"} in stage["filter"]["$and"]
    assert {"approval_status": "approved"} in stage["filter"]["$and"]
    assert {"is_latest": True} in stage["filter"]["$and"]
    assert p[1]["$project"]["embedding"] == 0 and p[1]["$project"]["score"] == {"$meta": "vectorSearchScore"}


def test_index_definition_fields():
    d = vs.index_definition(1536)["fields"]
    assert d[0] == {"type": "vector", "path": "embedding", "numDimensions": 1536, "similarity": "cosine"}
    assert {f["path"] for f in d[1:]} == {"team_id", "service_id", "status", "approval_status", "is_latest", "expires_at"}


def test_search_returns_eligible_and_uses_team_from_argument(repo, fdb):
    fdb.hits = [hit()]
    r = vs.search_memories(repo, EMB, TICKET, "team-alpha")
    assert r.mode == "atlas_vector" and r.refs == ["MEM-001@1"] and r.candidates == 1
    sent = fdb.pipelines[0][0]["$vectorSearch"]
    assert len(sent["queryVector"]) == 64 and {"team_id": "team-alpha"} in sent["filter"]["$and"]


def test_post_filter_rejects_wrong_team_expired_model_and_rule(repo, fdb):
    past = utcnow() - timedelta(days=1)
    fdb.hits = [hit(memory_id="M-A", team_id="team-beta"), hit(memory_id="M-B", expires_at=past),
                hit(memory_id="M-C", embedding_model="other"), hit(memory_id="M-D", rule_type="run_code"),
                hit(memory_id="M-E", status="retired"), hit(memory_id="M-F", is_latest=False),
                hit(memory_id="M-G", approval_status="pending"), hit(memory_id="M-H", service_id="other"),
                hit(memory_id="MEM-001")]
    r = vs.search_memories(repo, EMB, TICKET, "team-alpha")
    assert r.refs == ["MEM-001@1"] and len(r.rejected) == 8 and r.candidates == 9


def test_beta_gets_nothing_when_only_alpha_hits_leak(repo, fdb):
    fdb.hits = [hit()]  # even if the DB wrongly returned an alpha doc, app recheck drops it
    assert vs.search_memories(repo, EMB, TICKET, "team-beta").eligible == []


def test_missing_index_is_actionable(repo, fdb):
    fdb.indexes = []
    with pytest.raises(vs.VectorSearchError, match="setup-indexes"):
        vs.search_memories(repo, EMB, TICKET, "team-alpha")


def test_not_queryable_and_dimension_mismatch(repo, fdb):
    fdb.indexes = [{"name": "memory_vector_index", "queryable": False, "status": "BUILDING",
                    "latestDefinition": vs.index_definition(64)}]
    with pytest.raises(vs.VectorSearchError, match="not queryable"):
        vs.search_memories(repo, EMB, TICKET, "team-alpha")
    fdb.indexes = [{"name": "memory_vector_index", "queryable": True, "latestDefinition": vs.index_definition(1536)}]
    with pytest.raises(vs.VectorSearchError, match="numDimensions"):
        vs.search_memories(repo, EMB, TICKET, "team-alpha")


def test_query_failure_is_visible_not_silent(repo, fdb):
    fdb.hits = OperationFailure("boom", details={"errmsg": "index not found"})
    with pytest.raises(vs.VectorSearchError, match="index not found"):
        vs.search_memories(repo, EMB, TICKET, "team-alpha")


def test_embedding_failure_propagates(repo):
    def bad(_):
        raise EmbeddingError("down")
    with pytest.raises(EmbeddingError):
        vs.search_memories(repo, bad, TICKET, "team-alpha")


def test_ensure_search_index_creates_once(fdb):
    fdb.indexes = []
    vs.ensure_search_index(fdb, "memory_vector_index", 64, wait_seconds=1, poll=0.01)
    vs.ensure_search_index(fdb, "memory_vector_index", 64, wait_seconds=1, poll=0.01)
    assert len(fdb.created) == 1
    m = fdb.created[0].document
    assert m["type"] == "vectorSearch" and m["definition"]["fields"][0]["numDimensions"] == 64


def test_ensure_search_index_dimension_conflict(fdb):
    with pytest.raises(vs.VectorSearchError, match="numDimensions"):
        vs.ensure_search_index(fdb, "memory_vector_index", 1536, wait_seconds=1, poll=0.01)


def test_confirm_indexed_times_out_visibly(repo, fdb):
    fdb.hits = []
    with pytest.raises(vs.VectorSearchError, match="not returned"):
        vs.confirm_indexed(repo, EMB, TICKET, "team-alpha", "MEM-001@1", wait_seconds=0.05, poll=0.01)
    fdb.hits = [hit()]
    assert vs.confirm_indexed(repo, EMB, TICKET, "team-alpha", "MEM-001@1").refs == ["MEM-001@1"]


def test_stub_retriever_is_labeled_and_team_scoped():
    r = Repository(mongomock.MongoClient().stub, embedder=EMB)
    r.ensure_indexes()
    r.approve_new_memory(resolve_actor("alice"), SVC, RULE_DASHBOARD_REFRESH, "t", "a", "INC-MDB-017")
    st = vs.StubRetriever(r)
    a, b = st.search(TICKET, "team-alpha"), st.search(TICKET, "team-beta")
    assert a.mode == "test_stub" and a.refs == ["MEM-001@1"] and b.eligible == []


def test_hash_embedder_deterministic_and_dimensioned():
    a, b = EMB("dashboard refresh"), EMB("dashboard refresh")
    assert a == b and len(a["vector"]) == 64 and a["model"] == "test-local-hash"


def test_query_text_uses_source_facts_only():
    t = ticket_query_text(TICKET)
    assert SVC in t and "revenue_dashboard_refresh_job" in t


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_openai_embedder_request_and_validation(monkeypatch):
    seen = {}

    def fake_open(req, timeout):
        seen["body"] = json.loads(req.data)
        seen["auth"] = req.headers["Authorization"]
        return _Resp(json.dumps({"data": [{"embedding": [0.1] * 8}]}).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    out = OpenAIEmbedder("sk-test", "text-embedding-3-small", 8)("hi")
    assert out["dimensions"] == 8 and seen["body"]["dimensions"] == 8
    with pytest.raises(EmbeddingError, match="dimensions"):
        OpenAIEmbedder("sk-test", "m", 16)("hi")


def test_openai_embedder_errors_do_not_leak_key(monkeypatch):
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.HTTPError("u", 401, "bad", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(EmbeddingError) as e:
        OpenAIEmbedder("sk-secret-123", "m", 8)("x")
    assert "sk-secret-123" not in str(e.value) and "401" in str(e.value)
    with pytest.raises(EmbeddingError, match="OPENAI_API_KEY"):
        OpenAIEmbedder("", "m", 8)
