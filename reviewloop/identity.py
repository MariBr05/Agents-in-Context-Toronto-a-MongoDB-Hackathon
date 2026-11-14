"""Simulated demo identities. NOT real authentication: the --actor flag is
trusted as-is. Production would need verified identity tokens and
server-side authorization."""
from __future__ import annotations

from .models import Actor

_REGISTRY = {
    "alice": Actor(name="alice", team_id="team-alpha", role="reviewer"),
    "bob": Actor(name="bob", team_id="team-beta", role="developer"),
    "beth": Actor(name="beth", team_id="team-beta", role="reviewer"),
}


class UnknownActor(Exception):
    pass


class NotAuthorized(Exception):
    pass


def resolve_actor(name: str) -> Actor:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownActor("Unknown simulated actor %r; known: %s" % (name, ", ".join(sorted(_REGISTRY))))


def describe(actor: Actor) -> str:
    return "%s (%s, simulated identity)" % (actor.name, actor.team_id)


def require_memory_authority(actor: Actor, team_id: str) -> None:
    """Only reviewers may mutate memory, and only for their own team."""
    if actor.role != "reviewer":
        raise NotAuthorized("%s (role=%s) may not approve/correct/retire memory" % (actor.name, actor.role))
    if actor.team_id != team_id:
        raise NotAuthorized("%s (%s) may not modify memory of %s" % (actor.name, actor.team_id, team_id))


def all_actors():
    return list(_REGISTRY.values())
