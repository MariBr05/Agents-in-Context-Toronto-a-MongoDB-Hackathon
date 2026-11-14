"""Review orchestration (Milestone 1: baseline only; memory added later)."""
from __future__ import annotations

from typing import List

from .baseline import baseline_review
from .models import NEEDS_CHANGES, READY, Finding, Plan


def verdict_for(findings: List[Finding]) -> str:
    return NEEDS_CHANGES if findings else READY


def review_baseline_only(plan: Plan):
    findings = baseline_review(plan)
    return verdict_for(findings), findings
