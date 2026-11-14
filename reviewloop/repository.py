"""PyMongo repository: memory revision lifecycle + audit events.

Concurrency model (documented limitation): no multi-document transactions.
A correction first flips the current latest revision with a compare-and-set
update (`revision == N AND is_latest AND status == active`); only the winner
inserts revision N+1. A partial unique index on `memory_id` where
`is_latest == true` guarantees at most one latest revision per memory even if
two writers race. If the insert fails after the flip, the flip is reverted.

Embeddings are produced by an injected `embedder` (Milestone 3 supplies the real
provider). Tests inject an explicitly labeled stub.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from .identity import NotAuthorized, require_memory_authority
from .models import Actor
from .rules import VALIDATORS

MEMORY = "memory_revisions"
PROPOSALS = "memory_proposals"
RUNS = "review_runs"
AUDIT = "audit_events"
COUNTERS = "counters"

Embedder = Callable[[str], Dict[str, Any]]  # text -> {"vector": [...], "model": str, "dimensions": int}


class MemoryError_(Exception):
    pass


class NotFound(MemoryError_):
    pass


class Conflict(MemoryError_):
    pass


def utcnow() -> datetime:
    """UTC time as a naive datetime (what BSON round-trips)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def embedding_text(rule_text: str, required_action: str) -> str:
    """The memory's meaningful content that gets embedded."""
    return "%s\n%s" % (rule_text.strip(), required_action.strip())


class Repository:
    def __init__(self, db, embedder: Optional[Embedder] = None, clock: Callable[[], datetime] = utcnow):
        self.db = db
        self.embedder = embedder
        self.clock = clock

    # ---- setup / health -------------------------------------------------
    def ensure_indexes(self) -> None:
        m = self.db[MEMORY]
        m.create_index([("memory_id", 1), ("revision", 1)], unique=True, name="uniq_memory_revision")
        m.create_index([("memory_id", 1)], unique=True, name="uniq_latest_per_memory",
                       partialFilterExpression={"is_latest": True})
        m.create_index([("team_id", 1), ("service_id", 1), ("status", 1), ("approval_status", 1), ("is_latest", 1)],
                       name="scope_lookup")
        self.db[AUDIT].create_index([("created_at", 1)], name="audit_time")
        self.db[RUNS].create_index([("fixture_id", 1), ("reviewed_at", 1)], name="runs_by_fixture")

    def health_check(self) -> Dict[str, Any]:
        self.db.command("ping")
        names = set(self.db.list_collection_names())
        return {"ping": True, "collections_present": sorted(names & {MEMORY, PROPOSALS, RUNS, AUDIT})}

    # ---- ids ------------------------------------------------------------
    def _next(self, key: str) -> int:
        doc = self.db[COUNTERS].find_one_and_update({"_id": key}, {"$inc": {"seq": 1}},
                                                    upsert=True, return_document=True)
        return int(doc["seq"])

    # ---- audit ----------------------------------------------------------
    def audit(self, action: str, actor: Optional[Actor], target: Optional[str] = None,
              reason: Optional[str] = None, before: Any = None, after: Any = None,
              **extra: Any) -> str:
        event_id = "AUD-%06d" % self._next("audit")
        doc = {"event_id": event_id, "action": action,
               "actor": actor.name if actor else None, "team_id": actor.team_id if actor else None,
               "target": target, "before": before, "after": after, "reason": reason,
               "created_at": self.clock()}
        doc.update(extra)
        self.db[AUDIT].insert_one(doc)
        return event_id

    def _authorize(self, actor: Actor, team_id: str, action: str, target: Optional[str]) -> None:
        try:
            require_memory_authority(actor, team_id)
        except NotAuthorized as e:
            self.audit("authorization_denied", actor, target=target, reason=str(e), attempted=action)
            raise

    # ---- proposals ------------------------------------------------------
    def submit_proposal(self, actor: Actor, service_id: str, rule_type: str, rule_text: str,
                        required_action: str, source_ticket: str) -> str:
        """Create a PENDING proposal. Pending proposals never affect reviews."""
        if rule_type not in VALIDATORS:
            raise MemoryError_("Unsupported rule_type %r" % rule_type)
        if not (rule_text.strip() and required_action.strip() and source_ticket.strip()):
            raise MemoryError_("rule_text, required_action and source_ticket are required")
        pid = "PROP-%03d" % self._next("proposal")
        self.db[PROPOSALS].insert_one({
            "proposal_id": pid, "team_id": actor.team_id, "service_id": service_id,
            "rule_type": rule_type, "rule_text": rule_text, "required_action": required_action,
            "source_ticket": source_ticket, "submitted_by": actor.name,
            "approval_status": "pending", "created_at": self.clock()})
        self.audit("memory_submitted", actor, target=pid)
        return pid

    def approve_proposal(self, actor: Actor, proposal_id: str,
                         expires_at: Optional[datetime] = None) -> Dict[str, Any]:
        """Explicit human approval -> approved revision 1."""
        prop = self.db[PROPOSALS].find_one({"proposal_id": proposal_id})
        if not prop:
            raise NotFound("Proposal %s not found" % proposal_id)
        self._authorize(actor, prop["team_id"], "approve", proposal_id)
        if prop["approval_status"] != "pending":
            raise Conflict("Proposal %s is already %s" % (proposal_id, prop["approval_status"]))
        claimed = self.db[PROPOSALS].update_one(
            {"proposal_id": proposal_id, "approval_status": "pending"},
            {"$set": {"approval_status": "approved", "approved_by": actor.name, "approved_at": self.clock()}})
        if claimed.modified_count != 1:
            raise Conflict("Proposal %s was approved concurrently" % proposal_id)
        memory_id = "MEM-%03d" % self._next("memory")
        doc = self._insert_revision(
            memory_id, 1, prop["team_id"], prop["service_id"], prop["rule_type"], prop["rule_text"],
            prop["required_action"], prop["source_ticket"], actor, expires_at)
        self.audit("memory_approved", actor, target="%s@1" % memory_id, before=None,
                   after="%s@1" % memory_id, proposal_id=proposal_id)
        return doc

    def approve_new_memory(self, actor: Actor, service_id: str, rule_type: str, rule_text: str,
                           required_action: str, source_ticket: str,
                           expires_at: Optional[datetime] = None) -> Dict[str, Any]:
        """Submit + approve as one explicit human act (used by the demo command)."""
        pid = self.submit_proposal(actor, service_id, rule_type, rule_text, required_action, source_ticket)
        return self.approve_proposal(actor, pid, expires_at)

    # ---- revisions ------------------------------------------------------
    def _insert_revision(self, memory_id, revision, team_id, service_id, rule_type, rule_text,
                         required_action, source_ticket, actor, expires_at) -> Dict[str, Any]:
        if self.embedder is None:
            raise MemoryError_("No embedder configured; cannot persist an approved memory without an embedding")
        emb = self.embedder(embedding_text(rule_text, required_action))
        now = self.clock()
        doc = {
            "memory_id": memory_id, "revision": revision, "team_id": team_id, "service_id": service_id,
            "status": "active", "approval_status": "approved", "is_latest": True,
            "rule_type": rule_type, "rule_text": rule_text, "required_action": required_action,
            "source_ticket": source_ticket, "approved_by": actor.name,
            "created_at": now, "approved_at": now, "expires_at": _naive_utc(expires_at),
            "embedding_model": emb["model"], "embedding_dimensions": emb["dimensions"],
            "embedding": list(emb["vector"]),
        }
        if len(doc["embedding"]) != doc["embedding_dimensions"]:
            raise MemoryError_("Embedding length %d != declared dimensions %d"
                               % (len(doc["embedding"]), doc["embedding_dimensions"]))
        self.db[MEMORY].insert_one(doc)
        return doc

    def latest(self, memory_id: str) -> Dict[str, Any]:
        doc = self.db[MEMORY].find_one({"memory_id": memory_id, "is_latest": True})
        if not doc:
            raise NotFound("Memory %s not found" % memory_id)
        return doc

    def correct(self, actor: Actor, memory_id: str, reason: str, rule_text: str, required_action: str,
                source_ticket: Optional[str] = None, expires_at: Optional[datetime] = None) -> Dict[str, Any]:
        """Create revision N+1; supersede revision N."""
        cur = self.latest(memory_id)
        self._authorize(actor, cur["team_id"], "correct", memory_id)
        if cur["status"] != "active":
            raise Conflict("%s@%d is %s; only active memories can be corrected" % (memory_id, cur["revision"], cur["status"]))
        if not reason.strip():
            raise MemoryError_("A reason is required")
        n = cur["revision"]
        flipped = self.db[MEMORY].update_one(
            {"memory_id": memory_id, "revision": n, "is_latest": True, "status": "active"},
            {"$set": {"is_latest": False, "status": "superseded", "superseded_at": self.clock(),
                      "superseded_by": actor.name}})
        if flipped.modified_count != 1:
            raise Conflict("%s@%d changed concurrently; retry" % (memory_id, n))
        try:
            new = self._insert_revision(memory_id, n + 1, cur["team_id"], cur["service_id"], cur["rule_type"],
                                        rule_text, required_action, source_ticket or cur["source_ticket"],
                                        actor, expires_at)
        except Exception:
            self.db[MEMORY].update_one({"memory_id": memory_id, "revision": n},
                                       {"$set": {"is_latest": True, "status": "active"},
                                        "$unset": {"superseded_at": "", "superseded_by": ""}})
            raise
        self.audit("memory_corrected", actor, target="%s@%d" % (memory_id, n + 1), reason=reason,
                   before="%s@%d" % (memory_id, n), after="%s@%d" % (memory_id, n + 1))
        return new

    def retire(self, actor: Actor, memory_id: str, reason: str) -> bool:
        """Retire the latest revision. Idempotent: returns False if already retired."""
        cur = self.latest(memory_id)
        self._authorize(actor, cur["team_id"], "retire", memory_id)
        if not reason.strip():
            raise MemoryError_("A reason is required")
        if cur["status"] == "retired":
            return False
        res = self.db[MEMORY].update_one(
            {"memory_id": memory_id, "revision": cur["revision"], "is_latest": True, "status": {"$ne": "retired"}},
            {"$set": {"status": "retired", "retired_at": self.clock(), "retired_by": actor.name,
                      "retire_reason": reason}})
        if res.modified_count != 1:
            return False
        self.audit("memory_retired", actor, target="%s@%d" % (memory_id, cur["revision"]), reason=reason,
                   before="active", after="retired")
        return True

    def expire_due(self) -> int:
        """Explicitly mark expired active memories (history preserved, nothing deleted)."""
        now = self.clock()
        due = list(self.db[MEMORY].find({"status": "active", "expires_at": {"$ne": None, "$lte": now}}))
        n = 0
        for d in due:
            r = self.db[MEMORY].update_one({"_id": d["_id"], "status": "active"},
                                           {"$set": {"status": "expired", "expired_at": now}})
            if r.modified_count:
                n += 1
                self.audit("memory_expired", None, target="%s@%d" % (d["memory_id"], d["revision"]))
        return n

    # ---- reads ----------------------------------------------------------
    def is_eligible(self, doc: Dict[str, Any], team_id: str, service_id: str) -> bool:
        """Authoritative read-time eligibility check (also used to post-filter vector hits)."""
        exp = doc.get("expires_at")
        return (doc.get("team_id") == team_id and doc.get("service_id") == service_id
                and doc.get("status") == "active" and doc.get("approval_status") == "approved"
                and doc.get("is_latest") is True and (exp is None or exp > self.clock()))

    def eligible_memories(self, team_id: str, service_id: str) -> List[Dict[str, Any]]:
        """Non-vector eligibility listing (metadata only). For unit-test stub / admin views,
        NOT a substitute for Atlas $vectorSearch."""
        cur = self.db[MEMORY].find({"team_id": team_id, "service_id": service_id, "status": "active",
                                    "approval_status": "approved", "is_latest": True}, {"embedding": 0})
        return [d for d in cur if self.is_eligible(d, team_id, service_id)]

    def other_team_memory_count(self, team_id: str, service_id: str) -> int:
        """Display-only count of active memory revisions belonging to OTHER teams (never applied)."""
        return self.db[MEMORY].count_documents({"team_id": {"$ne": team_id}, "service_id": service_id,
                                                "status": "active", "is_latest": True})

    def review_runs(self, fixture_id: str) -> List[Dict[str, Any]]:
        return list(self.db[RUNS].find({"fixture_id": fixture_id}).sort("review_id", 1))

    def history(self, memory_id: str) -> List[Dict[str, Any]]:
        return list(self.db[MEMORY].find({"memory_id": memory_id}, {"embedding": 0}).sort("revision", 1))

    def list_memories(self, team_id: str) -> List[Dict[str, Any]]:
        return list(self.db[MEMORY].find({"team_id": team_id}, {"embedding": 0}).sort(
            [("memory_id", 1), ("revision", 1)]))

    def audit_events(self, **filt) -> List[Dict[str, Any]]:
        return list(self.db[AUDIT].find(filt).sort("event_id", 1))
