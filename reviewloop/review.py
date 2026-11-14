"""Review orchestration: baseline rubric + deterministic enforcement of retrieved, approved memories.

Vector retrieval only nominates candidate memories. Whether a memory applies (scope re-check) and
whether the plan satisfies it (typed validator) are decided by explicit code. Memory text is data,
never instructions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .baseline import baseline_review
from .models import NEEDS_CHANGES, READY, Actor, ChangeRequest, Finding, Plan
from .plan_hash import InputError, plan_sha256
from .repository import RUNS, Repository
from .rules import RULE_FINDINGS, VALIDATORS


def verdict_for(findings: List[Finding]) -> str:
    return NEEDS_CHANGES if findings else READY


def review_baseline_only(plan: Plan):
    findings = baseline_review(plan)
    return verdict_for(findings), findings


def memory_findings(plan: Plan, memories: List[Dict[str, Any]], team_id: str, service_id: str) -> List[Finding]:
    out = []
    for m in memories:
        # defensive re-check even though retrieval already filtered
        if m.get("team_id") != team_id or m.get("service_id") != service_id or m["rule_type"] not in VALIDATORS:
            continue
        if VALIDATORS[m["rule_type"]](plan) is None:
            code, missing, step = RULE_FINDINGS[m["rule_type"]]
            out.append(Finding(code=code, missing=missing, suggested_step=step, source_type="memory",
                               evidence="No verification step satisfies rule %s." % m["rule_type"],
                               memory_id=m["memory_id"], memory_revision=m["revision"],
                               source_ticket=m.get("source_ticket")))
    return out


def run_review(repo: Repository, retriever, actor: Actor, ticket: ChangeRequest, plan: Plan,
               persist: bool = True) -> Dict[str, Any]:
    """Retrieval errors propagate: live mode never degrades silently to baseline-only."""
    if plan.fixture_id != ticket.fixture_id:
        raise InputError("Plan fixture_id %r does not match ticket fixture_id %r" % (plan.fixture_id, ticket.fixture_id))
    digest = plan_sha256(plan)
    base = baseline_review(plan)
    repo.expire_due()
    retrieval = retriever.search(ticket, actor.team_id)  # team comes from the resolved actor only
    mem = memory_findings(plan, retrieval.eligible, actor.team_id, ticket.service_id)
    findings = base + mem
    refs = retrieval.refs
    result = {
        "fixture_id": ticket.fixture_id, "actor": actor.name, "team_id": actor.team_id,
        "service_id": ticket.service_id, "plan_sha256": digest, "verdict": verdict_for(findings),
        "retrieval_mode": retrieval.mode, "candidates_returned": retrieval.candidates,
        "retrieved_memory_revisions": refs, "applied_memory_revisions": refs,
        "rejected_candidates": retrieval.rejected, "baseline_finding_count": len(base),
        "findings": [f.model_dump() for f in findings],
        "excluded_other_team_memories": repo.other_team_memory_count(actor.team_id, ticket.service_id),
    }
    if persist:
        result["review_id"] = "REV-%04d" % repo._next("review")
        result["reviewed_at"] = repo.clock()
        repo.db[RUNS].insert_one(dict(result))
        repo.audit("review_executed", actor, target=result["review_id"], after=result["verdict"],
                   plan_sha256=digest, retrieval_mode=retrieval.mode, applied=refs)
    return result


def format_review(r: Dict[str, Any]) -> str:
    live = {"atlas_vector": "Atlas", "test_stub": "TEST STUB (not Atlas)"}[r["retrieval_mode"]]
    lines = ["Actor: %s (%s, simulated identity)" % (r["actor"], r["team_id"]),
             "Fixture: %s | Plan SHA256: %s" % (r["fixture_id"], r["plan_sha256"]),
             "Vector retrieval: %s | Eligible memories: %d" % (live, len(r["applied_memory_revisions"])),
             "Baseline findings: %d" % r["baseline_finding_count"]]
    if not r["applied_memory_revisions"]:
        lines.append("Memory: no eligible approved memory found for this team/service")
    for f in r["findings"]:
        if f["source_type"] == "memory":
            lines.append("Applied memory: %s@%s (%s)" % (f["memory_id"], f["memory_revision"], f["source_ticket"]))
    lines.append("Verdict: %s" % r["verdict"])
    for f in r["findings"]:
        lines += ["Finding: " + f["code"], "  Missing: " + f["missing"], "  Add: " + f["suggested_step"]]
        if f["source_type"] == "memory":
            lines.append("  Provenance: %s@%s, reviewer-approved feedback from %s"
                         % (f["memory_id"], f["memory_revision"], f["source_ticket"]))
        else:
            lines.append("  Provenance: baseline rubric")
    if r.get("excluded_other_team_memories"):
        lines.append("Excluded: %d memory revision(s) from other teams are outside actor's authorized team scope"
                     % r["excluded_other_team_memories"])
    if r.get("review_id"):
        lines.append("Review run: %s (recorded)" % r["review_id"])
    lines.append("Note: READY FOR REVIEW means ready for human examination, not approved to deploy.")
    return "\n".join(lines)
