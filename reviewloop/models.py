"""Pydantic data contracts."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

READY = "READY FOR REVIEW"
NEEDS_CHANGES = "NEEDS CHANGES"


class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id: str
    service_id: str
    change_type: str
    target_environment: str
    proposed_change: str
    known_dependencies: List[str] = Field(default_factory=list)
    deployment_artifact: Optional[str] = None
    prior_version: Optional[str] = None
    new_version: Optional[str] = None


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id: str
    implementation: List[str]
    rollback: List[str]
    verification: List[str]


class Finding(BaseModel):
    code: str
    missing: str
    suggested_step: str
    source_type: str  # "baseline" | "memory"
    evidence: str = ""
    memory_id: Optional[str] = None
    memory_revision: Optional[int] = None
    source_ticket: Optional[str] = None


class Actor(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    team_id: str
    role: str  # "reviewer" | "developer"
