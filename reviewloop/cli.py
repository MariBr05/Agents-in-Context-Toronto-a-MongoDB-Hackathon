"""ReviewLoop CLI. Actor identities are SIMULATED (not authentication); the team always comes from the
resolved actor, never from a flag."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict

from . import config
from .demo import DEMO_FIXTURE, approve_demo_feedback, run_demo
from .embeddings import EmbeddingError, HashTestEmbedder, get_embedder
from .identity import NotAuthorized, UnknownActor, resolve_actor, all_actors
from .plan_hash import InputError, load_plan, load_ticket, plan_sha256
from .repository import MemoryError_, Repository
from .review import format_review, run_review
from .vector_search import AtlasRetriever, StubRetriever, VectorSearchError, confirm_indexed, ensure_search_index, index_status

KNOWN_ERRORS = (config.ConfigError, EmbeddingError, VectorSearchError, InputError, NotAuthorized, UnknownActor,
                MemoryError_)


def _live():
    s = config.load_settings()
    emb = get_embedder()
    if emb.dimensions != s.embedding_dimensions:
        raise config.ConfigError("EMBEDDING_DIMENSIONS mismatch")
    repo = Repository(config.connect(s), embedder=emb)
    return s, emb, repo


def _j(o: Any) -> str:
    return json.dumps(o, indent=2, default=lambda x: x.isoformat() if isinstance(x, datetime) else str(x))


def cmd_doctor(a):
    s, emb, repo = _live()
    print("Config: OK (secrets not shown)")
    print("Atlas ping: %s" % repo.health_check())
    v = emb("doctor check")
    print("Embedding: model=%s dimensions=%d" % (v["model"], v["dimensions"]))
    print("Vector index: %s" % index_status(repo.db, s.vector_index, s.embedding_dimensions))


def cmd_setup(a):
    s, emb, repo = _live()
    repo.ensure_indexes()
    ensure_search_index(repo.db, s.vector_index, s.embedding_dimensions)
    print("Regular indexes ensured; vector index %r is queryable (%d dims)." % (s.vector_index, s.embedding_dimensions))


def cmd_seed(a):
    s, emb, repo = _live()
    repo.ensure_indexes()
    ticket, plan = load_ticket(a.ticket), load_plan(a.plan)
    for ac in all_actors():
        repo.db["demo_actors"].update_one({"_id": ac.name}, {"$set": ac.model_dump()}, upsert=True)
    repo.db["demo_fixtures"].update_one({"_id": ticket.fixture_id}, {"$set": {
        "service_id": ticket.service_id, "plan_sha256": plan_sha256(plan)}}, upsert=True)
    print("Seeded %d simulated actors and fixture %s (idempotent; reviews untouched)." % (len(all_actors()), ticket.fixture_id))


def cmd_review(a):
    s, emb, repo = _live()
    actor = resolve_actor(a.actor)
    r = run_review(repo, AtlasRetriever(repo, emb, s.vector_index), actor, load_ticket(a.ticket), load_plan(a.plan))
    print(_j(r) if a.json else format_review(r))


def cmd_memory(a):
    s, emb, repo = _live()
    actor = resolve_actor(a.actor)
    ticket = load_ticket(a.ticket)
    if a.mcmd == "approve-demo-feedback":
        mem = approve_demo_feedback(repo, actor, ticket.service_id)
        ref = "%s@%d" % (mem["memory_id"], mem["revision"])
        confirm_indexed(repo, emb, ticket, actor.team_id, ref, s.vector_index)
        print("Authorized reviewer: %s (human-approved act)\nApproved memory: %s\nScope: %s / %s\nSource: %s\n"
              "Rule: %s\nAtlas: persisted and indexed" % (actor.name, ref, mem["team_id"], mem["service_id"],
                                                          mem["source_ticket"], mem["rule_type"]))
    elif a.mcmd == "correct":
        rf = json.load(open(a.rule_file))
        exp = datetime.fromisoformat(rf["expires_at"]) if rf.get("expires_at") else None
        new = repo.correct(actor, a.id, a.reason, rf["rule_text"], rf["required_action"], rf.get("source_ticket"), exp)
        print("Created %s@%d (previous revision superseded)" % (new["memory_id"], new["revision"]))
    elif a.mcmd == "retire":
        print("Retired %s" % a.id if repo.retire(actor, a.id, a.reason) else "%s was already retired" % a.id)


def cmd_memories(a):
    s, emb, repo = _live()
    actor = resolve_actor(a.actor)
    for m in repo.list_memories(actor.team_id):  # only the actor's own team
        print("%s@%d  %-10s latest=%s  %s  %s" % (m["memory_id"], m["revision"], m["status"], m["is_latest"],
                                                  m["rule_type"], m["source_ticket"]))


def cmd_history(a):
    s, emb, repo = _live()
    for r in repo.review_runs(a.fixture):
        print("%s  %s/%s  %s  mode=%s  applied=%s  sha=%s..." % (r["review_id"], r["actor"], r["team_id"], r["verdict"],
              r["retrieval_mode"], r["applied_memory_revisions"], r["plan_sha256"][:12]))


def cmd_demo(a):
    from .repository import Repository as R
    if a.offline_stub:
        import mongomock
        repo = R(mongomock.MongoClient().reviewloop_stub, embedder=HashTestEmbedder(64))
        repo.ensure_indexes()
        print("*** OFFLINE TEST STUB: in-memory fake DB, hash embeddings, NO Atlas, NO Vector Search ***")
        ok = run_demo(repo, StubRetriever(repo), a.ticket, a.plan, a.reset_demo_data, None, as_json=a.json)
    else:
        s, emb, repo = _live()
        ticket = load_ticket(a.ticket)
        ok = run_demo(repo, AtlasRetriever(repo, emb, s.vector_index), a.ticket, a.plan, a.reset_demo_data,
                      lambda ref: confirm_indexed(repo, emb, ticket, "team-alpha", ref, s.vector_index),
                      as_json=a.json)
    return 0 if ok else 1


def build_parser():
    T, P = "fixtures/mongodb_aggregation_change.json", "fixtures/mongodb_baseline_plan.json"
    p = argparse.ArgumentParser(prog="reviewloop")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sp.add_parser("setup-indexes").set_defaults(fn=cmd_setup)
    s = sp.add_parser("seed-demo"); s.add_argument("--ticket", default=T); s.add_argument("--plan", default=P); s.set_defaults(fn=cmd_seed)
    r = sp.add_parser("review"); r.add_argument("--actor", required=True); r.add_argument("--ticket", required=True)
    r.add_argument("--plan", required=True); r.add_argument("--json", action="store_true"); r.set_defaults(fn=cmd_review)
    m = sp.add_parser("memory"); ms = m.add_subparsers(dest="mcmd", required=True); m.set_defaults(fn=cmd_memory)
    d = ms.add_parser("approve-demo-feedback"); d.add_argument("--actor", required=True); d.add_argument("--ticket", default=T)
    c = ms.add_parser("correct"); c.add_argument("--actor", required=True); c.add_argument("--id", required=True)
    c.add_argument("--reason", required=True); c.add_argument("--rule-file", required=True); c.add_argument("--ticket", default=T)
    t = ms.add_parser("retire"); t.add_argument("--actor", required=True); t.add_argument("--id", required=True)
    t.add_argument("--reason", required=True); t.add_argument("--ticket", default=T)
    q = sp.add_parser("memories"); q.add_argument("--actor", required=True); q.set_defaults(fn=cmd_memories)
    h = sp.add_parser("history"); h.add_argument("--fixture", default=DEMO_FIXTURE); h.set_defaults(fn=cmd_history)
    e = sp.add_parser("demo"); e.add_argument("--reset-demo-data", action="store_true"); e.add_argument("--json", action="store_true")
    e.add_argument("--offline-stub", action="store_true", help="TEST ONLY: in-memory fake, not Atlas")
    e.add_argument("--ticket", default=T); e.add_argument("--plan", default=P); e.set_defaults(fn=cmd_demo)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a) or 0
    except KNOWN_ERRORS as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
