import json
from pathlib import Path

import pytest

from reviewloop.identity import NotAuthorized, UnknownActor, require_memory_authority, resolve_actor
from reviewloop.models import NEEDS_CHANGES, READY, Plan
from reviewloop.plan_hash import InputError, load_plan, load_ticket, plan_sha256
from reviewloop.review import review_baseline_only

FIX = Path(__file__).resolve().parent.parent / "fixtures"
PLAN = FIX / "mongodb_baseline_plan.json"
TICKET = FIX / "mongodb_aggregation_change.json"


def plan_with(**over):
    d = json.loads(PLAN.read_text())
    d.update(over)
    return Plan.model_validate(d)


def test_stage_a_fixture_ready_for_review():
    verdict, findings = review_baseline_only(load_plan(PLAN))
    assert verdict == READY and findings == []


def test_hash_stable_and_key_order_independent():
    p = load_plan(PLAN)
    h = plan_sha256(p)
    assert h == plan_sha256(load_plan(PLAN)) == plan_sha256(load_plan(PLAN))
    assert len(h) == 64


def test_hash_changes_when_plan_changes():
    assert plan_sha256(load_plan(PLAN)) != plan_sha256(plan_with(verification=["x"]))


def test_fixture_does_not_mention_downstream_job_in_plan():
    # deliberate omission required by the demo
    text = PLAN.read_text().lower()
    assert "dashboard" not in text and "downstream" not in text


@pytest.mark.parametrize("field,code", [
    ("implementation", "MISSING_IMPLEMENTATION"),
    ("rollback", "MISSING_ROLLBACK_TRIGGER"),
    ("verification", "MISSING_BASIC_SMOKE_TEST"),
])
def test_empty_section_flags_baseline(field, code):
    verdict, findings = review_baseline_only(plan_with(**{field: []}))
    assert verdict == NEEDS_CHANGES
    assert code in {f.code for f in findings}
    assert all(f.source_type == "baseline" and f.suggested_step for f in findings)


def test_missing_prereq_and_restore_and_recovery():
    p = plan_with(implementation=["Deploy pipeline-v2 to staging."],
                  rollback=["If it fails, stop the rollout."])
    codes = {f.code for f in review_baseline_only(p)[1]}
    assert {"MISSING_PREREQUISITES", "MISSING_ROLLBACK_RESTORE", "MISSING_RECOVERY_VERIFICATION"} <= codes


def test_prerequisite_after_publish_is_flagged():
    p = plan_with(implementation=["Deploy pipeline-v2 to staging.", "Confirm workspace access and backup exist."])
    assert "MISSING_PREREQUISITES" in {f.code for f in review_baseline_only(p)[1]}


def test_smoke_test_without_criterion_flagged():
    p = plan_with(verification=["Run a basic smoke test."])
    assert "MISSING_BASIC_SMOKE_TEST" in {f.code for f in review_baseline_only(p)[1]}


def test_missing_ticket_field_is_clear_error(tmp_path):
    d = json.loads(TICKET.read_text())
    del d["target_environment"]
    f = tmp_path / "t.json"
    f.write_text(json.dumps(d))
    with pytest.raises(InputError, match="target_environment"):
        load_ticket(f)


def test_missing_file_and_bad_json(tmp_path):
    with pytest.raises(InputError):
        load_plan(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    with pytest.raises(InputError):
        load_plan(bad)


def test_ticket_loads():
    assert load_ticket(TICKET).service_id == "mongodb-revenue-aggregation"


def test_identity_registry():
    assert resolve_actor("alice").team_id == "team-alpha"
    with pytest.raises(UnknownActor):
        resolve_actor("mallory")


def test_memory_authority():
    require_memory_authority(resolve_actor("alice"), "team-alpha")
    with pytest.raises(NotAuthorized):
        require_memory_authority(resolve_actor("bob"), "team-beta")  # developer
    with pytest.raises(NotAuthorized):
        require_memory_authority(resolve_actor("beth"), "team-alpha")  # other team


# --- typed rule validator (pure; memory retrieval comes in later milestones) ---
from reviewloop.rules import check_dashboard_refresh  # noqa: E402


def test_aggregation_only_smoke_test_does_not_satisfy_dashboard_rule():
    assert check_dashboard_refresh(load_plan(PLAN)) is None


def test_dashboard_refresh_with_completion_and_totals_satisfies_rule():
    p = plan_with(verification=load_plan(PLAN).verification + [
        "Trigger the downstream revenue dashboard refresh job and confirm it completes successfully.",
        "Compare dashboard revenue totals with expected staging values.",
    ])
    assert check_dashboard_refresh(p)
    assert review_baseline_only(p)[0] == READY


def test_dashboard_refresh_missing_totals_or_completion_fails():
    only_run = plan_with(verification=["Trigger the revenue dashboard refresh job."])
    assert check_dashboard_refresh(only_run) is None
    no_totals = plan_with(verification=["Trigger the revenue dashboard refresh job and confirm it completes."])
    assert check_dashboard_refresh(no_totals) is None


def test_plan_hash_identical_across_three_simulated_reviews():
    hashes = [plan_sha256(load_plan(PLAN)) for _ in range(3)]
    assert len(set(hashes)) == 1
