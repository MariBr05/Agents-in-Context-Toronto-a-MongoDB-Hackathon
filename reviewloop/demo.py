"""Three-stage demo with invariants. Exits non-zero (returns False) if any invariant fails."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from .identity import resolve_actor
from .models import NEEDS_CHANGES, READY
from .plan_hash import load_plan, load_ticket
from .repository import MEMORY, PROPOSALS, RUNS, Repository
from .review import format_review, run_review
from .rules import CODE_DASHBOARD_REFRESH, RULE_DASHBOARD_REFRESH

DEMO_FIXTURE = "MDB-CHG-001"
DEMO_SOURCE_TICKET = "INC-MDB-017"
FEEDBACK_TEXT = ("A previous MongoDB daily-revenue aggregation update passed its pipeline smoke test but broke the "
                 "downstream revenue dashboard refresh job. Future changes to this aggregation pipeline must run the "
                 "dashboard refresh job and verify successful completion and expected dashboard totals before sign-off.")
REQUIRED_ACTION = ("Run the downstream revenue-dashboard refresh job and verify successful completion and expected "
                   "dashboard totals.")


def approve_demo_feedback(repo: Repository, actor, service_id: str) -> Dict[str, Any]:
    return repo.approve_new_memory(actor, service_id, RULE_DASHBOARD_REFRESH, FEEDBACK_TEXT, REQUIRED_ACTION,
                                   DEMO_SOURCE_TICKET)


def reset_demo_data(repo: Repository, service_id: str) -> Dict[str, int]:
    """Delete ONLY demo-scoped documents (this fixture / service / incident). Audit events are append-only and kept."""
    n = {
        "memory_revisions": repo.db[MEMORY].delete_many({"service_id": service_id, "source_ticket": DEMO_SOURCE_TICKET}).deleted_count,
        "memory_proposals": repo.db[PROPOSALS].delete_many({"service_id": service_id, "source_ticket": DEMO_SOURCE_TICKET}).deleted_count,
        "review_runs": repo.db[RUNS].delete_many({"fixture_id": DEMO_FIXTURE}).deleted_count,
    }
    if repo.db[MEMORY].count_documents({}) == 0:  # only restart ids when nothing else uses them
        repo.db["counters"].delete_many({"_id": {"$in": ["memory", "proposal"]}})
    repo.audit("demo_reset", None, reason="deleted demo-scoped documents only", after=n)
    return n


def run_demo(repo: Repository, retriever, ticket_path, plan_path, reset: bool, confirm_indexed: Callable = None,
             out: Callable[[str], None] = print, as_json: bool = False) -> bool:
    ticket, plan = load_ticket(ticket_path), load_plan(plan_path)
    alice, bob = resolve_actor("alice"), resolve_actor("bob")
    say = (lambda s: None) if as_json else out
    if reset:
        say("== Reset (demo-scoped documents only): %s" % reset_demo_data(repo, ticket.service_id))

    say("\n== Stage A: alice reviews the plan BEFORE any approved memory")
    a = run_review(repo, retriever, alice, ticket, plan)
    say(format_review(a))

    say("\n== Stage B: alice (reviewer) explicitly approves feedback")
    mem = approve_demo_feedback(repo, alice, ticket.service_id)
    ref = "%s@%d" % (mem["memory_id"], mem["revision"])
    indexed = True
    if confirm_indexed:
        confirm_indexed(ref)
    else:
        indexed = False
    say("Authorized reviewer: alice (human-approved act)\nApproved memory: %s\nScope: %s / %s\nSource: %s\n"
        "Rule: %s\nStored: %s" % (ref, mem["team_id"], mem["service_id"], mem["source_ticket"], mem["rule_type"],
                                  "persisted and confirmed retrievable by vector search" if indexed
                                  else "persisted (vector retrievability NOT confirmed: stub mode)"))

    say("\n== Stage C: alice reviews the SAME plan again")
    c = run_review(repo, retriever, alice, ticket, plan)
    say(format_review(c))

    say("\n== Stage D: bob (team-beta) reviews the SAME plan")
    d = run_review(repo, retriever, bob, ticket, plan)
    say(format_review(d))

    hashes = [r["plan_sha256"] for r in (a, c, d)]
    verdicts = [r["verdict"] for r in (a, c, d)]
    cf = c["findings"]
    checks = [
        ("three plan hashes identical", len(set(hashes)) == 1),
        ("verdict progression READY -> NEEDS CHANGES -> READY", verdicts == [READY, NEEDS_CHANGES, READY]),
        ("stage A used no memory and no baseline findings", a["applied_memory_revisions"] == [] and a["baseline_finding_count"] == 0),
        ("stage C applied exactly %s" % ref, c["applied_memory_revisions"] == [ref] and mem["revision"] == 1),
        ("stage C finding is %s citing %s / %s" % (CODE_DASHBOARD_REFRESH, ref, DEMO_SOURCE_TICKET),
         len(cf) == 1 and cf[0]["code"] == CODE_DASHBOARD_REFRESH and cf[0]["memory_id"] == mem["memory_id"]
         and cf[0]["memory_revision"] == 1 and cf[0]["source_ticket"] == DEMO_SOURCE_TICKET),
        ("stage D (team-beta) applied no memory", d["applied_memory_revisions"] == [] and d["findings"] == []),
        ("retrieval mode consistent", len({r["retrieval_mode"] for r in (a, c, d)}) == 1),
    ]
    ok = all(v for _, v in checks)
    summary = {"ok": ok, "retrieval_mode": a["retrieval_mode"], "plan_sha256": hashes, "verdicts": verdicts,
               "memory": ref, "source_ticket": DEMO_SOURCE_TICKET, "review_ids": [r["review_id"] for r in (a, c, d)],
               "checks": [{"check": n, "passed": v} for n, v in checks]}
    if as_json:
        out(json.dumps(summary, indent=2))
    else:
        say("\n== Evidence summary (retrieval mode: %s)" % a["retrieval_mode"])
        say("Plan SHA256 x3: %s" % ("identical " + hashes[0] if len(set(hashes)) == 1 else "MISMATCH " + str(hashes)))
        say("Verdicts: " + " -> ".join(verdicts))
        say("Memory provenance: %s from %s" % (ref, DEMO_SOURCE_TICKET))
        for n, v in checks:
            say("[%s] %s" % ("PASS" if v else "FAIL", n))
        say("DEMO %s" % ("PASSED" if ok else "FAILED"))
    return ok
