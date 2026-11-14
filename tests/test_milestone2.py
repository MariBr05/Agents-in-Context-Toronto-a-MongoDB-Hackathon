"""Lifecycle/authorization tests. Use mongomock (an in-memory fake, NOT Atlas)
and a labeled stub embedder; they prove app logic, not Atlas integration."""
from datetime import timedelta

import mongomock
import pytest

from reviewloop.identity import NotAuthorized, resolve_actor
from reviewloop.repository import Conflict, MemoryError_, NotFound, Repository, utcnow
from reviewloop.rules import RULE_DASHBOARD_REFRESH

SVC = "mongodb-revenue-aggregation"
TEXT = "A previous aggregation update broke the downstream revenue dashboard refresh job."
ACTION = "Run the dashboard refresh job and verify completion and totals."
ALICE, BOB, BETH = (resolve_actor(n) for n in ("alice", "bob", "beth"))


def stub_embedder(text):  # explicitly a TEST stub, 8 dims
    return {"vector": [float(len(text) % 7)] * 8, "model": "test-stub", "dimensions": 8}


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def repo(clock):
    r = Repository(mongomock.MongoClient().reviewloop_test, embedder=stub_embedder, clock=clock)
    r.ensure_indexes()
    return r


def approve(repo, actor=ALICE, **kw):
    return repo.approve_new_memory(actor, SVC, RULE_DASHBOARD_REFRESH, TEXT, ACTION, "INC-MDB-017", **kw)


def test_approve_creates_mem_001_rev1_with_audit(repo):
    d = approve(repo)
    assert (d["memory_id"], d["revision"]) == ("MEM-001", 1)
    assert d["status"] == "active" and d["is_latest"] and d["approval_status"] == "approved"
    assert d["team_id"] == "team-alpha" and d["source_ticket"] == "INC-MDB-017" and d["approved_by"] == "alice"
    assert len(d["embedding"]) == d["embedding_dimensions"] == 8
    assert [e["action"] for e in repo.audit_events()] == ["memory_submitted", "memory_approved"]


def test_developer_cannot_approve_and_denial_is_audited(repo):
    pid = repo.submit_proposal(BOB, SVC, RULE_DASHBOARD_REFRESH, TEXT, ACTION, "INC-MDB-017")
    with pytest.raises(NotAuthorized):
        repo.approve_proposal(BOB, pid)
    assert repo.db["memory_revisions"].count_documents({}) == 0
    assert repo.audit_events(action="authorization_denied")[0]["actor"] == "bob"


def test_other_team_reviewer_cannot_approve_correct_or_retire(repo):
    d = approve(repo)
    with pytest.raises(NotAuthorized):
        repo.correct(BETH, d["memory_id"], "x", "t", "a")
    with pytest.raises(NotAuthorized):
        repo.retire(BETH, d["memory_id"], "x")
    pid = repo.submit_proposal(ALICE, SVC, RULE_DASHBOARD_REFRESH, TEXT, ACTION, "INC-2")
    with pytest.raises(NotAuthorized):
        repo.approve_proposal(BETH, pid)
    assert repo.latest("MEM-001")["revision"] == 1
    assert len(repo.audit_events(action="authorization_denied")) == 3


def test_pending_proposal_has_no_effect(repo):
    repo.submit_proposal(ALICE, SVC, RULE_DASHBOARD_REFRESH, TEXT, ACTION, "INC-MDB-017")
    assert repo.eligible_memories("team-alpha", SVC) == []


def test_unknown_rule_type_rejected(repo):
    with pytest.raises(MemoryError_):
        repo.submit_proposal(ALICE, SVC, "run_arbitrary_code", TEXT, ACTION, "INC-1")


def test_double_approval_conflicts(repo):
    pid = repo.submit_proposal(ALICE, SVC, RULE_DASHBOARD_REFRESH, TEXT, ACTION, "INC-1")
    repo.approve_proposal(ALICE, pid)
    with pytest.raises(Conflict):
        repo.approve_proposal(ALICE, pid)
    assert repo.db["memory_revisions"].count_documents({}) == 1


def test_eligibility_is_team_and_service_scoped(repo):
    approve(repo)
    assert [d["memory_id"] for d in repo.eligible_memories("team-alpha", SVC)] == ["MEM-001"]
    assert repo.eligible_memories("team-beta", SVC) == []
    assert repo.eligible_memories("team-alpha", "other-service") == []


def test_correction_creates_rev2_and_supersedes_rev1(repo):
    approve(repo)
    new = repo.correct(ALICE, "MEM-001", "Policy revision", TEXT + " (v2)", ACTION)
    assert new["revision"] == 2 and new["is_latest"] and new["status"] == "active"
    hist = repo.history("MEM-001")
    assert [(h["revision"], h["status"], h["is_latest"]) for h in hist] == [(1, "superseded", False), (2, "active", True)]
    assert [d["revision"] for d in repo.eligible_memories("team-alpha", SVC)] == [2]
    ev = repo.audit_events(action="memory_corrected")[0]
    assert (ev["before"], ev["after"], ev["reason"]) == ("MEM-001@1", "MEM-001@2", "Policy revision")


def test_cannot_have_two_latest_revisions(repo):
    approve(repo)
    from pymongo.errors import DuplicateKeyError
    dup = dict(repo.latest("MEM-001"))
    dup.pop("_id")
    dup["revision"] = 2
    with pytest.raises(DuplicateKeyError):
        repo.db["memory_revisions"].insert_one(dup)


def test_failed_correction_restores_previous_revision(repo):
    approve(repo)
    repo.embedder = lambda t: (_ for _ in ()).throw(RuntimeError("embedding down"))
    with pytest.raises(RuntimeError):
        repo.correct(ALICE, "MEM-001", "r", "t", "a")
    cur = repo.latest("MEM-001")
    assert (cur["revision"], cur["status"]) == (1, "active")
    assert len(repo.eligible_memories("team-alpha", SVC)) == 1


def test_correct_requires_reason_and_active(repo):
    approve(repo)
    with pytest.raises(MemoryError_):
        repo.correct(ALICE, "MEM-001", "  ", "t", "a")
    repo.retire(ALICE, "MEM-001", "obsolete")
    with pytest.raises(Conflict):
        repo.correct(ALICE, "MEM-001", "r", "t", "a")


def test_retire_is_idempotent_and_keeps_history(repo):
    approve(repo)
    assert repo.retire(ALICE, "MEM-001", "No longer applicable") is True
    assert repo.retire(ALICE, "MEM-001", "again") is False
    assert repo.eligible_memories("team-alpha", SVC) == []
    assert repo.history("MEM-001")[0]["status"] == "retired"
    assert len(repo.audit_events(action="memory_retired")) == 1


def test_expired_memory_ignored_on_read_and_marked(repo, clock):
    approve(repo, expires_at=clock.now + timedelta(days=1))
    assert len(repo.eligible_memories("team-alpha", SVC)) == 1
    clock.now += timedelta(days=2)
    assert repo.eligible_memories("team-alpha", SVC) == []      # read-time check, before any status update
    assert repo.expire_due() == 1 and repo.expire_due() == 0    # idempotent
    assert repo.history("MEM-001")[0]["status"] == "expired"    # history preserved
    assert repo.audit_events(action="memory_expired")


def test_no_embedder_refuses_to_persist(clock):
    r = Repository(mongomock.MongoClient().db2, embedder=None, clock=clock)
    with pytest.raises(MemoryError_):
        approve(r)


def test_not_found(repo):
    with pytest.raises(NotFound):
        repo.retire(ALICE, "MEM-999", "x")


def test_list_memories_scoped_by_team_and_no_embedding_leak(repo):
    approve(repo)
    assert all("embedding" not in d for d in repo.list_memories("team-alpha"))
    assert repo.list_memories("team-beta") == []


def test_health_check(repo):
    assert repo.health_check()["ping"] is True
