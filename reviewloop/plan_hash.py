"""Canonical plan hashing and fixture loading."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from pydantic import ValidationError

from .models import ChangeRequest, Plan


class InputError(Exception):
    pass


def canonical_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def plan_sha256(plan: Plan) -> str:
    return hashlib.sha256(canonical_json(plan.model_dump()).encode("utf-8")).hexdigest()


def _load(path, model):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError("File not found: %s" % path)
    except json.JSONDecodeError as e:
        raise InputError("Invalid JSON in %s: %s" % (path, e))
    try:
        return model.model_validate(data)
    except ValidationError as e:
        problems = "; ".join("%s: %s" % (".".join(map(str, err["loc"])) or "<root>", err["msg"]) for err in e.errors())
        raise InputError("Invalid %s in %s (no values were invented): %s" % (model.__name__, path, problems))


def load_ticket(path) -> ChangeRequest:
    return _load(path, ChangeRequest)


def load_plan(path) -> Plan:
    return _load(path, Plan)
