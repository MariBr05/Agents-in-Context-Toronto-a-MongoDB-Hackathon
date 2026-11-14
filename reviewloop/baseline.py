"""Deterministic baseline rubric. No LLM, no memory lookups.

Limitation: predicates are keyword/regex based over natural-language steps.
They are tuned for the demo fixture and are not a general NLP checker.
"""
from __future__ import annotations

import re
from typing import Callable, List, Tuple

from .models import Finding, Plan

_PUBLISH = re.compile(r"\b(publish|deploy|import|release|promote)\b", re.I)
_PREREQ = re.compile(r"\b(confirm|verify|check|ensure)\b.*\b(access|backup|prerequisite|permission)", re.I)
_TRIGGER = re.compile(r"\b(if|when|upon)\b.*\b(fail|incorrect|error|mismatch|regress)", re.I)
_STOP = re.compile(r"\b(stop|halt|abort|revert|roll\s?back)\b", re.I)
_RESTORE = re.compile(r"\b(restore|revert|roll\s?back to)\b.*\b(previous|prior|backup|v\d|[\w-]+-v\d)", re.I)
_RERUN = re.compile(r"\b(re-?run|run|re-?execute|execute|re-?test|re-?verify)\b", re.I)
_RECOVERY_CHECK = re.compile(r"\b(confirm|verify|check|assert|compare)\b", re.I)
_SMOKE = re.compile(r"\bsmoke test\b", re.I)
_CRITERION = re.compile(r"\b(succeed|succeeds|succeeded|success|pass|passes|match|matches|expected|equal)\b", re.I)


def _find(steps: List[str], *patterns) -> str:
    for s in steps:
        if all(p.search(s) for p in patterns):
            return s
    return ""


def _order_ok(plan: Plan) -> str:
    """Publish step must come after a prerequisite step (ordering)."""
    pre_idx = next((i for i, s in enumerate(plan.implementation) if _PREREQ.search(s)), None)
    pub_idx = next((i for i, s in enumerate(plan.implementation) if _PUBLISH.search(s)), None)
    if pub_idx is not None and (pre_idx is None or pre_idx < pub_idx):
        return plan.implementation[pub_idx]
    return ""


# (code, missing, suggested fix, evidence-finder)
_CHECKS: List[Tuple[str, str, str, Callable[[Plan], str]]] = [
    ("MISSING_IMPLEMENTATION",
     "No ordered publish/deploy action in the implementation section.",
     "Add ordered deployment steps.",
     lambda p: _find(p.implementation, _PUBLISH)),
    ("MISSING_PREREQUISITES",
     "No prerequisite/access/backup check before deployment.",
     "Verify access, backups and prerequisites before publishing.",
     lambda p: _find(p.implementation, _PREREQ) if _order_ok(p) else ""),
    ("MISSING_ROLLBACK_TRIGGER",
     "Rollback does not define a condition to stop or revert.",
     "Define concrete failure triggers that stop the rollout.",
     lambda p: _find(p.rollback, _TRIGGER, _STOP)),
    ("MISSING_ROLLBACK_RESTORE",
     "Rollback does not restore the previous version.",
     "Describe the exact action that restores the previous version.",
     lambda p: _find(p.rollback, _RESTORE)),
    ("MISSING_RECOVERY_VERIFICATION",
     "Rollback does not verify recovery after restoring.",
     "Re-run the prior version and compare results.",
     lambda p: _find(p.rollback, _RERUN, _RECOVERY_CHECK)),
    ("MISSING_BASIC_SMOKE_TEST",
     "Verification lacks a smoke test with a pass criterion.",
     "Execute a smoke test and assert expected output.",
     lambda p: _find(p.verification, _SMOKE) and _find(p.verification, _CRITERION)),
]


def baseline_review(plan: Plan) -> List[Finding]:
    findings = []
    for code, missing, fix, evidence in _CHECKS:
        found = evidence(plan)
        if not found:
            findings.append(Finding(code=code, missing=missing, suggested_step=fix,
                                    source_type="baseline", evidence="No matching step found."))
    return findings
