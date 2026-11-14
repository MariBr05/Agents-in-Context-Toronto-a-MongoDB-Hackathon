"""LIVE Atlas integration tests (marker: atlas). Need MONGODB_URI + OPENAI_API_KEY in .env and a
queryable `memory_vector_index` (run `python -m reviewloop.cli setup-indexes` first).
Skipped WITH AN EXPLICIT REASON when credentials are absent; a skip is NOT a pass.
Only touches memories it creates (team-alpha demo scope) and retires them afterwards."""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()
HAVE = all(os.environ.get(k, "").strip() and "<" not in os.environ[k] for k in ("MONGODB_URI", "OPENAI_API_KEY")) \
    if all(k in os.environ for k in ("MONGODB_URI", "OPENAI_API_KEY")) else False
pytestmark = [pytest.mark.atlas,
              pytest.mark.skipif(not HAVE, reason="ATLAS NOT VERIFIED: MONGODB_URI/OPENAI_API_KEY not configured")]

from reviewloop import config, vector_search as vs  # noqa: E402
from reviewloop.embeddings import get_embedder  # noqa: E402
from reviewloop.identity import resolve_actor  # noqa: E402
from reviewloop.plan_hash import load_ticket  # noqa: E402
from reviewloop.repository import Repository  # noqa: E402
from reviewloop.rules import RULE_DASHBOARD_REFRESH  # noqa: E402

TICKET = load_ticket(Path(__file__).resolve().parent.parent / "fixtures" / "mongodb_aggregation_change.json")


@pytest.fixture(scope="module")
def live():
    s = config.load_settings()
    emb = get_embedder()
    repo = Repository(config.connect(s), embedder=emb)
    repo.ensure_indexes()
    vs.index_status(repo.db, s.vector_index, s.embedding_dimensions)
    return repo, emb, s


def test_vector_search_returns_alpha_memory_and_excludes_beta(live):
    repo, emb, s = live
    alice = resolve_actor("alice")
    doc = repo.approve_new_memory(
        alice, TICKET.service_id, RULE_DASHBOARD_REFRESH,
        "A previous MongoDB daily-revenue aggregation update passed its pipeline smoke test but broke the "
        "downstream revenue dashboard refresh job.",
        "Run the downstream revenue-dashboard refresh job and verify successful completion and expected totals.",
        "INC-MDB-ITEST")
    ref = "%s@%d" % (doc["memory_id"], doc["revision"])
    try:
        assert len(doc["embedding"]) == s.embedding_dimensions
        r = vs.confirm_indexed(repo, emb, TICKET, "team-alpha", ref, s.vector_index)
        assert r.mode == "atlas_vector" and ref in r.refs
        assert ref not in vs.search_memories(repo, emb, TICKET, "team-beta", s.vector_index).refs
    finally:
        repo.retire(alice, doc["memory_id"], "integration test cleanup")


def test_retired_memory_is_not_retrieved(live):
    repo, emb, s = live
    alice = resolve_actor("alice")
    doc = repo.approve_new_memory(alice, TICKET.service_id, RULE_DASHBOARD_REFRESH,
                                  "Dashboard refresh must be verified.", "Run dashboard refresh.", "INC-MDB-ITEST2")
    ref = "%s@%d" % (doc["memory_id"], doc["revision"])
    vs.confirm_indexed(repo, emb, TICKET, "team-alpha", ref, s.vector_index)
    repo.retire(alice, doc["memory_id"], "integration test cleanup")
    import time
    deadline = time.time() + 60  # index propagation of the status change is asynchronous
    while ref in vs.search_memories(repo, emb, TICKET, "team-alpha", s.vector_index).refs:
        assert time.time() < deadline, "retired memory still returned"
        time.sleep(2)
