"""Typed memory-rule validators (pure functions; no DB, no LLM).

Memory text is never executed or treated as instructions: only the typed
`rule_type` selects a validator from this fixed table.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Optional

from .models import Plan

RULE_DASHBOARD_REFRESH = "require_downstream_dashboard_refresh_test"
CODE_DASHBOARD_REFRESH = "MISSING_DOWNSTREAM_DASHBOARD_REFRESH_TEST"
DASHBOARD_REFRESH_SUGGESTION = (
    "After deploying pipeline-v2, trigger the downstream revenue dashboard refresh job; "
    "verify successful completion and compare the displayed revenue totals with known "
    "expected staging values before sign-off."
)
DASHBOARD_REFRESH_MISSING = (
    "The plan tests the aggregation result but does not execute or validate the "
    "dependent dashboard-refresh job."
)

_DASHBOARD = re.compile(r"dashboard", re.I)
_REFRESH_RUN = re.compile(r"\b(run|trigger|execute|start)\b.*\brefresh\b|\brefresh\b.*\b(job|run)\b", re.I)
_COMPLETION = re.compile(r"\b(succe\w+|complet\w+|finish\w+)\b", re.I)
_TOTALS = re.compile(r"\b(totals?|output|values?)\b.*\b(match|compare|expected|equal)\w*|\b(match|compare|expected|equal)\w*\b.*\b(totals?|output|values?)\b", re.I)


def check_dashboard_refresh(plan: Plan) -> Optional[str]:
    """Return evidence step if satisfied, else None.

    Requires, in the verification section, a dashboard-refresh job execution
    AND completion check AND dashboard totals check. An aggregation-only
    smoke test does not satisfy it.
    """
    steps = plan.verification
    has_run = any(_DASHBOARD.search(s) and _REFRESH_RUN.search(s) for s in steps)
    dash_steps = [s for s in steps if _DASHBOARD.search(s)]
    has_completion = any(_COMPLETION.search(s) for s in dash_steps)
    has_totals = any(_TOTALS.search(s) for s in dash_steps)
    if has_run and has_completion and has_totals:
        return " | ".join(dash_steps)
    return None


VALIDATORS: Dict[str, Callable[[Plan], Optional[str]]] = {
    RULE_DASHBOARD_REFRESH: check_dashboard_refresh,
}
