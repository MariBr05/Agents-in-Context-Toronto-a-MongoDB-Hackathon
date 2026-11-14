# ReviewLoop — Governed Change-Ticket Agent

**Claude Code implementation specification | Hackathon MVP | Version 1.0**

> **Primary instruction to Claude Code:** Implement the small, runnable Python CLI described here. Work incrementally, inspect the current repository before editing, and deliver working code, tests, an Atlas Vector Search integration, and a repeatable live demo. Do not add a web frontend, Databricks integration, or deployment execution. Do not claim a live Atlas demo passed unless it actually ran against Atlas.

## 1. Executive summary

ReviewLoop is a human-in-the-loop AI assistant for deployment/change tickets. It accepts a change request (service, proposed change, target environment, known dependencies), drafts three plan sections (Implementation, Rollback, Verification), and checks the plan against a **baseline rubric** plus relevant, **approved, active, team-and-service-scoped reviewer memories** stored in MongoDB Atlas.

The reviewer-facing result is exactly one of `READY FOR REVIEW` and `NEEDS CHANGES`. Each finding must state the missing item, a concrete step to add, its provenance (`baseline` or a specific immutable memory revision), and the evidence that the draft does or does not contain it. Human reviewers can approve new memories, correct existing memories by creating a new revision, and retire them. Memory application must be traceable and must not cross team boundaries.

The demo uses **one synthetic Databricks notebook deployment fixture**, not real Databricks. First review the unchanged draft for **team-alpha**: it includes a basic smoke test and meets the baseline rubric, so the result is `READY FOR REVIEW`. Next an authorized reviewer approves an incident-derived rule: a prior notebook update broke the downstream reporting job, so future notebook deployments must run a downstream reporting smoke test. Review the **identical draft bytes** as team-alpha again: result changes to `NEEDS CHANGES`, with a specific downstream test and the exact memory ID/revision. Finally review the same draft as **team-beta**: it is `READY FOR REVIEW`, because team-alpha memory must not leak into team-beta retrieval.

**Central proof:** The same plan produces a different review verdict only because a scoped, approved memory was added in MongoDB Atlas.

## 2. Non-goals and guardrails

- This is a CLI-only hackathon MVP. No web UI, real Databricks APIs, real deployments, automated rollback, CI/CD orchestration, or production claims.
- LLM-generated content is an editable **draft**; the application never executes deployment steps.
- The review status is **not** permission to deploy: `READY FOR REVIEW` means ready for human examination, not approved for production.
- Do not treat raw ticket text, retrieved memory text, or LLM output as privileged instructions. No automatic approvals and no automatic memory creation from an unapproved comment.
- For the live demo, preserve one frozen fixture and one stable baseline rubric. Do not silently patch the plan between evaluations.
- Avoid overengineering: no Kubernetes, microservices, event bus, frontend, or complex auth server.

## 3. User roles and trust model

Two demo identities are seeded locally. These identities are **simulated** and suitable only for a demo; changing a CLI `--actor` flag is not real authentication. Production deployment would require verified identity tokens, server-side authorization, and database access controls.

| Actor | Team | Role | Can review | Can approve/correct/retire memory |
|---|---|---|---|---|
| `alice` | `team-alpha` | reviewer | Yes, team-alpha | Yes, team-alpha only |
| `bob` | `team-beta` | developer | Yes, team-beta | No |
| `beth` | `team-beta` | reviewer | Yes, team-beta | Yes, team-beta only |

For every CLI command, resolve actor details from a server-controlled/local seeded registry, **not** from `--team` supplied by the caller. The actor's team determines the memory filter. Reject attempts to mutate another team's memory. In logs and output, identify the simulated actor clearly. Never say this mechanism is secure authentication.

## 4. Precise definitions

- **Change request:** Source facts: service ID, proposed change, environment, dependencies. If information required to propose a safe plan is missing, return a targeted finding asking the developer to supply it. Never invent environment or deployment details.
- **Plan:** A versioned, immutable-for-review object with `implementation`, `rollback`, `verification` lists. The demo fixture itself is not rewritten during review.
- **Baseline rubric:** Fixed checklist enforced deterministically without fetching memories (e.g., required deployment steps, rollback trigger/restoration/recovery verification, and basic smoke test). The seeded demo plan must pass baseline.
- **Memory rule:** An approved reviewer requirement such as “notebook deployments for reporting-related services must test the downstream reporting job.”
- **Memory revision:** Immutable record of one specific version of a rule (e.g., `MEM-001@1`). An updated rule becomes `MEM-001@2`; old revisions remain in history for auditing but are not retrieved as active rules.
- **Finding:** Gap in a plan with a proposed insertion and source provenance.
- **Review event:** Immutable record of plan digest, actor/team, verdict, findings, memory revisions applied, and timestamp.

## 5. Functional requirements

### FR1 — Draft
A CLI command takes a structured request JSON and writes a plan JSON with exactly three sections. A configurable LLM client may draft these sections. Provide a deterministic template fallback for tests/demo and when no LLM API key is available. Never state an LLM was used if the fallback was used. Missing key request fields cause findings or clear CLI validation errors, not fabricated details.

### FR2 — Baseline review
Enforce checks with deterministic predicates rather than prompting an LLM for a free-form verdict. At minimum: (a) implementation contains deployment ordering and a prerequisite check, (b) rollback includes trigger, restoring previous version, and recovery check, (c) verification includes a basic smoke test and a success criterion. Return `NEEDS CHANGES` for any unmet requirement with a concrete suggested step. Demo fixture must fulfill every baseline check.

### FR3 — Governed memories
Reviewers can approve a new memory, correct it to create a new revision, retire it, and optionally set an expiration date. Store source ticket/incident, approval actor, scope, timestamps, approval state, rule type, action required, and embedding metadata. Only approved, active, unexpired, latest revisions may affect a review. Never use unapproved submissions.

### FR4 — MongoDB Atlas vector retrieval
Generate embeddings of an approved memory's *meaningful content* and of the current change request/service context using the **same embedding model and dimensionality**. Search with `$vectorSearch`, with server-constructed team/service filters inside the stage. Exclude inactive or superseded rules and expired rules. Do not substitute a local keyword search while claiming Atlas Vector Search was used. If Atlas or the vector index is unavailable, fail visibly for live mode; unit tests can use an explicit in-memory stub.

### FR5 — Deterministic memory enforcement
Vector similarity finds candidate memories; it does not decide whether a memory applies or whether the plan satisfies it. After retrieval, check metadata scope, approval, revision status, expiry, rule applicability, and presence of required test with explicit checks. Use a typed `rule_type` such as `require_downstream_reporting_smoke_test`. For the demo, the test predicate should only pass when a **downstream reporting job test** is actually present—not when the plan merely says “run a smoke test.”

### FR6 — Auditability
Every finding records source type and exact `memory_id`, revision, and source incident where relevant. Record every review run, including `plan_sha256`, `retrieval_mode` (`atlas_vector` or `test_stub`), the memory revision IDs retrieved, those applied, verdict, findings, actor/team, and UTC time. Make it clear when no relevant memory is found. Audit memory approval, correction and retirement actions separately.

### FR7 — Team isolation
`team-alpha` memories must never be applied to `team-beta` reviews, even when the service and plan are identical. Scope must be enforced during the Atlas vector query, and rechecked after retrieval. No CLI option should override the authenticated/simulated identity's team.

### FR8 — CLI, docs, tests
Expose commands for setup, fixture seeding, drafting, reviewing, submitting/approving/correcting/retiring memory, inspecting history, and running the demo. Include `.env.example`, `README.md`, `pytest` tests, and clear instructions to configure Atlas without committing credentials.

## 6. Suggested architecture

```text
reviewloop CLI (Typer or argparse)
  ├── identity.py       seeded demo actor resolver + permission checks
  ├── models.py         Pydantic data contracts
  ├── draft.py          optional LLM drafting + template fallback
  ├── baseline.py       deterministic baseline checklist
  ├── rules.py          typed memory-rule applicability + plan checks
  ├── embeddings.py     configured embedding provider and model metadata
  ├── repository.py     PyMongo CRUD, revision lifecycle, audit events
  ├── vector_search.py  Atlas $vectorSearch with strict metadata pre-filter
  ├── review.py         review orchestration, verdict and provenance
  └── demo.py           immutable three-stage scenario, assertions and output
MongoDB Atlas
  ├── memory_revisions (vector index + metadata)
  ├── review_runs
  └── audit_events
fixtures/databricks_notebook_change.json
fixtures/databricks_baseline_plan.json
```

Use Python 3.11+; `pymongo`, `pydantic`, `python-dotenv`, `pytest`, and either `typer` or `argparse`. If using external embeddings, make the provider pluggable (`EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, API key). Prefer a real embedding API for Atlas; an explicitly labeled local test embedder is acceptable for unit tests only. A text-generation API is optional for MVP demonstration because the deterministic fixture proves the core memory behavior.

## 7. Data design

### `memory_revisions`

Each revision is a separate immutable document; updating a memory creates a new revision. Use atomic transitions or transactions where supported to prevent two active revisions for the same logical memory. If transactions are unsupported in the sandbox tier, use optimistic concurrency and a unique active-pointer strategy, and document the limitations.

```json
{
  "memory_id": "MEM-001",
  "revision": 1,
  "team_id": "team-alpha",
  "service_id": "databricks-reporting-notebook",
  "status": "active",
  "approval_status": "approved",
  "is_latest": true,
  "rule_type": "require_downstream_reporting_smoke_test",
  "rule_text": "A prior notebook update broke the downstream reporting job. For subsequent notebook updates, verify the downstream reporting job runs and produces expected results before sign-off.",
  "required_action": "Run a downstream reporting-job smoke test and validate its completion status and expected report output.",
  "source_ticket": "INC-DBX-017",
  "approved_by": "alice",
  "created_at": "<UTC ISO timestamp stored as BSON datetime>",
  "approved_at": "<UTC BSON datetime>",
  "expires_at": null,
  "embedding_model": "<chosen model>",
  "embedding_dimensions": 1536,
  "embedding": [0.01, -0.02]
}
```

**Note:** `embedding` here is a **truncated illustrative array**, never a valid 1536-dimensional embedding. Real stored vectors must match the chosen provider's exact dimensions. Store native BSON dates, not timestamp strings, for filter comparisons.

On correction: mark previous revision `is_latest=false`/`status=superseded`, insert revision 2 with updated text and regenerated embedding; record an audit event. On retirement: mark latest revision inactive and record the reason. Expiry is enforced on reads by `expires_at is null OR expires_at > now` and, preferably, an explicit status update; do not rely solely on TTL deletion because revision history must persist.

Indexes: unique compound `(memory_id, revision)`; helpful B-tree index `(team_id, service_id, status, approval_status, is_latest)`; vector index described below. If you store a derived `eligible_for_search` boolean, update it on approval/revision/retirement/expiry, but still validate actual expiry at read time.

### `review_runs`

```json
{
  "review_id": "REV-0002",
  "fixture_id": "DBX-CHG-001",
  "actor": "alice",
  "team_id": "team-alpha",
  "service_id": "databricks-reporting-notebook",
  "plan_sha256": "<sha256 of canonical plan JSON>",
  "verdict": "NEEDS CHANGES",
  "retrieval_mode": "atlas_vector",
  "retrieved_memory_revisions": ["MEM-001@1"],
  "applied_memory_revisions": ["MEM-001@1"],
  "findings": [
    {
      "code": "MISSING_DOWNSTREAM_REPORTING_SMOKE_TEST",
      "missing": "The plan checks notebook execution but not the dependent reporting job.",
      "suggested_step": "After notebook deployment, trigger the downstream reporting job; require successful completion and verify expected report output before sign-off.",
      "source_type": "memory",
      "memory_id": "MEM-001",
      "memory_revision": 1,
      "source_ticket": "INC-DBX-017"
    }
  ],
  "reviewed_at": "<UTC BSON datetime>"
}
```

### `audit_events`

Append-only logical history of memory submissions, approvals, edits, retirements, failed authorization attempts, and review executions: event ID, action, actor, team, target memory/revision, before/after revision pointers, reason, UTC time. Do not persist secrets or raw API keys.

## 8. Atlas Vector Search implementation

1. Provision the hackathon's MongoDB Atlas Sandbox cluster (ensure the chosen deployment supports Vector Search). Get its connection URI and allow the required network access; grant only necessary database permissions. Use a dedicated database such as `reviewloop_demo`.
2. Create memory revision documents with full embeddings, stable embedding-model metadata, and BSON dates.
3. Create a Vector Search index on `memory_revisions`. Use a **BYO-vector** index with the vector field and every metadata field used for pre-filtering. **Do not use auto-embedding index syntax unless deliberately switching the architecture.**

**Example index definition** (adjust dimensions to the selected embedding model):

```json
{
  "fields": [
    { "type": "vector", "path": "embedding", "numDimensions": 1536, "similarity": "cosine" },
    { "type": "filter", "path": "team_id" },
    { "type": "filter", "path": "service_id" },
    { "type": "filter", "path": "status" },
    { "type": "filter", "path": "approval_status" },
    { "type": "filter", "path": "is_latest" },
    { "type": "filter", "path": "expires_at" }
  ]
}
```

Name the index `memory_vector_index`. Create it through Atlas UI or PyMongo's `SearchIndexModel` and wait until it is queryable. Index creation can be asynchronous.

**Sample aggregation** (illustrative Python; real implementation must handle null expiry safely and recheck on read):

```python
pipeline = [
    {"$vectorSearch": {
        "index": "memory_vector_index",
        "path": "embedding",
        "queryVector": query_embedding,
        "numCandidates": 100,
        "limit": 10,
        "filter": {
            "$and": [
                {"team_id": actor.team_id},
                {"service_id": ticket.service_id},
                {"status": "active"},
                {"approval_status": "approved"},
                {"is_latest": True}
            ]
        }
    }},
    {"$project": {
        "embedding": 0,
        "memory_id": 1, "revision": 1, "rule_type": 1,
        "rule_text": 1, "required_action": 1,
        "source_ticket": 1, "team_id": 1, "service_id": 1,
        "status": 1, "approval_status": 1,
        "is_latest": 1, "expires_at": 1,
        "score": {"$meta": "vectorSearchScore"}
    }}
]
```

**Post-filter and validation required:** exclude any document with an `expires_at` before `now`, unexpected team/service, invalid approval/status/latest flags, wrong embedding metadata, or unsupported `rule_type`. Where supported, optional expiry conditions can also be applied inside the vector pre-filter; post-check is mandatory either way. Do not let a similarity threshold alone override applicability. For the single fixture, the approved memory should be strongly relevant. Log candidate count and final applied revision IDs.

**Important edge case:** `limit` is applied during retrieval, so post-filtering expired results might yield fewer eligible matches. For MVP, either fetch more candidates and recheck, or maintain a current eligibility flag carefully. Make the limitations explicit.

**Atlas docs:**
- https://www.mongodb.com/docs/vector-search/indexes/vector-search-type/
- https://www.mongodb.com/docs/vector-search/query/aggregation-stages/vector-search-stage/

## 9. Exact synthetic fixture (do not change between reviews)

Create `fixtures/databricks_notebook_change.json`:

```json
{
  "fixture_id": "DBX-CHG-001",
  "service_id": "databricks-reporting-notebook",
  "change_type": "notebook_update",
  "target_environment": "staging",
  "proposed_change": "Update the customer revenue aggregation notebook to calculate daily totals using revised transformation logic.",
  "known_dependencies": ["downstream_reporting_job"],
  "deployment_artifact": "notebooks/customer_revenue_daily",
  "prior_version": "notebook-v1",
  "new_version": "notebook-v2"
}
```

Create `fixtures/databricks_baseline_plan.json` as the exact same plan for all three review calls:

```json
{
  "fixture_id": "DBX-CHG-001",
  "implementation": [
    "Confirm workspace access and notebook-v1 backup exist before deployment.",
    "Publish notebook-v2 to the staging workspace after the prerequisite checks."
  ],
  "rollback": [
    "If notebook execution fails or expected daily totals are incorrect, stop the rollout.",
    "Restore notebook-v1 from the saved backup.",
    "Rerun notebook-v1 and confirm successful execution and expected daily totals."
  ],
  "verification": [
    "Run a basic smoke test of notebook-v2 with a small staging input dataset.",
    "Confirm notebook execution succeeds and output daily totals match expected fixture values."
  ]
}
```

**Deliberate omission:** The baseline plan DOES NOT trigger or check the `downstream_reporting_job`. Mentioning the dependency in the request must **not** automatically add a baseline requirement for this fixture, otherwise the intended initial `READY FOR REVIEW` cannot be demonstrated. The learning experiment tests the impact of later reviewer feedback, not a universal dependency policy.

Freeze the baseline plan file. Compute its SHA-256 on canonical JSON (sort keys, stable separators) for each review and assert all three digests match. This is a non-negotiable demo invariant.

## 10. Baseline rubric and typed rules

### Baseline checks

Implement deterministic, testable checks using structured step categories or precise predicates. Avoid fragile substring-only assumptions where possible. Since the fixture is known, you may parse/verbalize canonical steps for the demo, but document the limitations for arbitrary natural language.

| Code | Required evidence | If absent, proposed fix |
|---|---|---|
| `MISSING_IMPLEMENTATION` | ordered publish/deploy action | Add ordered deployment steps |
| `MISSING_PREREQUISITES` | prerequisite/access/backup check | Verify access, backups and prerequisites |
| `MISSING_ROLLBACK_TRIGGER` | condition to stop/revert | Define concrete failure triggers |
| `MISSING_ROLLBACK_RESTORE` | restore previous version | Describe exact restore action |
| `MISSING_RECOVERY_VERIFICATION` | verify recovery after restore | Re-run prior version and compare results |
| `MISSING_BASIC_SMOKE_TEST` | smoke test + pass criterion | Execute smoke test, assert expected output |

### Approved memory rule: `require_downstream_reporting_smoke_test`

- Applies to `service_id=databricks-reporting-notebook`, relevant notebook updates, and the **reviewing actor's team**.
- Detect absence of a downstream reporting-job execution **and** a check of successful completion/expected report output.
- If missing, emit code `MISSING_DOWNSTREAM_REPORTING_SMOKE_TEST`.
- Suggest: “After notebook-v2 deployment, trigger the downstream reporting job; confirm the job finishes successfully and that its report output matches expected staging values before sign-off.”
- Attach `MEM-001@1` and source `INC-DBX-017` to the finding.
- Do not satisfy the requirement using the existing notebook-only smoke test.

For more general future rules, introduce new typed validators. Do not let arbitrary memory text become executable code or an unrestricted instruction to the LLM.

## 11. Memory approval and lifecycle

Initial state: no approved relevant team-alpha memory. The first review must explicitly verify that the memory search returned no eligible rule.

Reviewer `alice` submits the synthetic feedback:

> “A previous customer revenue notebook update completed successfully but broke the downstream reporting job. Future updates to this service must run the downstream reporting job and validate its output as part of verification.”

Approval workflow:
1. Create a pending memory proposal with team/service scope and incident `INC-DBX-017`.
2. Validate reviewer authorization.
3. On explicit approval, construct typed rule `require_downstream_reporting_smoke_test`, generate embedding, persist approved revision `MEM-001@1`, and record audit event.
4. Confirm the Atlas document exists and the vector index can retrieve it with team-alpha filters.
5. When correcting a memory, create revision 2 and exclude revision 1 from new reviews. Keep revision 1 in historical review events.
6. Retire/expire memory without deleting audit history. A subsequent review should not apply it.

The demo may combine submission + approval in one explicit `memory approve-demo-feedback --actor alice` command, but the output must clearly indicate the human-approved act. Never auto-approve from an arbitrary draft comment.

## 12. CLI interface and usage

Implement command names close to the following (document any deliberate differences). All commands must emit readable output, and review/demo should support optional `--json` for machine tests.

```bash
# 0. Clone/open existing project directory, create a venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in MONGODB_URI and embedding credentials; do not commit .env

# 1. Verify connectivity and indexes
python -m reviewloop.cli doctor
python -m reviewloop.cli setup-indexes

# 2. Seed identities and fixture (idempotent, do not overwrite prior reviews)
python -m reviewloop.cli seed-demo

# 3. First review (team-alpha; no approved feedback)
python -m reviewloop.cli review --actor alice \
  --ticket fixtures/databricks_notebook_change.json \
  --plan fixtures/databricks_baseline_plan.json

# 4. Authorized reviewer creates approved feedback in Atlas
python -m reviewloop.cli memory approve-demo-feedback --actor alice

# 5. EXACT same ticket and plan again (team-alpha)
python -m reviewloop.cli review --actor alice \
  --ticket fixtures/databricks_notebook_change.json \
  --plan fixtures/databricks_baseline_plan.json

# 6. EXACT same ticket and plan, different team
python -m reviewloop.cli review --actor bob \
  --ticket fixtures/databricks_notebook_change.json \
  --plan fixtures/databricks_baseline_plan.json

# 7. Show provenance and past review runs
python -m reviewloop.cli history --fixture DBX-CHG-001
python -m reviewloop.cli memories --actor alice

# 8. One command to run and assert the whole story
python -m reviewloop.cli demo --reset-demo-data

# Optional additional workflows
python -m reviewloop.cli draft --actor alice \
  --ticket fixtures/databricks_notebook_change.json \
  --output /tmp/drafted_plan.json
python -m reviewloop.cli memory correct --actor alice --id MEM-001 --reason 'Policy revision' --rule-file /path/to/revision.json
python -m reviewloop.cli memory retire --actor alice --id MEM-001 --reason 'No longer applicable'
pytest -q
```

`--reset-demo-data` must be **safely restricted** to documents in the dedicated demo namespace/IDs. Require an additional confirmation if it could touch non-demo data; do not drop arbitrary Atlas databases/collections. Prefer deterministic names for synthetic artifacts.

## 13. Exact expected demonstration outputs

These are the target semantics; formatting can differ.

### Stage A — Before memory

```text
Actor: alice (team-alpha, simulated identity)
Fixture: DBX-CHG-001 | Plan SHA256: <HASH>
Vector retrieval: Atlas | Eligible memories: 0
Baseline findings: 0
Verdict: READY FOR REVIEW
```

### Stage B — Approved feedback

```text
Authorized reviewer: alice
Approved memory: MEM-001@1
Scope: team-alpha / databricks-reporting-notebook
Source: INC-DBX-017
Rule: require_downstream_reporting_smoke_test
Atlas: persisted and indexed
```

### Stage C — Same plan after memory

```text
Actor: alice (team-alpha, simulated identity)
Fixture: DBX-CHG-001 | Plan SHA256: <SAME HASH>
Vector retrieval: Atlas | Eligible memories: 1
Applied memory: MEM-001@1 (INC-DBX-017)
Verdict: NEEDS CHANGES
Finding: MISSING_DOWNSTREAM_REPORTING_SMOKE_TEST
Missing: The verification plan does not run or validate the downstream reporting job.
Add: Trigger the downstream reporting job after deployment, verify successful completion,
     and compare report output with expected staging results.
Provenance: MEM-001@1, reviewer-approved feedback from INC-DBX-017
```

### Stage D — Different team, same plan

```text
Actor: bob (team-beta, simulated identity)
Fixture: DBX-CHG-001 | Plan SHA256: <SAME HASH>
Vector retrieval: Atlas | Eligible memories: 0
Baseline findings: 0
Verdict: READY FOR REVIEW
Excluded: team-alpha memory is outside actor's authorized team scope
```

The CLI must print an evidence summary including three equal plan hashes, the verdict progression `READY FOR REVIEW → NEEDS CHANGES → READY FOR REVIEW`, and the memory provenance. It must **exit non-zero** if any invariant fails. The same plan and ticket input files must be used throughout; no hidden edits to files or code between stages.

## 14. Testing strategy

### Fast deterministic unit tests (no external services)

- Baseline fixture returns `READY FOR REVIEW` with empty memory stub.
- Presence of approved `MEM-001@1` changes alpha verdict to `NEEDS CHANGES` with the exact finding code and suggested action.
- Generic notebook smoke test does **not** count as downstream reporting smoke test.
- A downstream job test **with** job success and expected output checks satisfies memory rule.
- Same draft hashes unchanged across three runs.
- Bob/team-beta cannot retrieve or apply alpha memory.
- Developer cannot approve memory; reviewer from other team cannot edit it.
- Pending, retired, superseded, and expired memories have no effect.
- Correction makes revision 2 active; review references revision 2 only, while old review history retains revision 1.
- Missing ticket fields produce clear errors/findings without invented values.
- Retrieval or embedding failure in live mode is reported clearly, not silently converted into a baseline-only success.
- Tests for injection-like memory text that tries to override scope or verdict are ignored as instructions.

### Atlas integration tests (require explicit config)

- `doctor` can connect and verify index readiness.
- Approved memory is inserted with full-dimensional embedding.
- `$vectorSearch` returns `MEM-001@1` for team-alpha/service query.
- `$vectorSearch` returns **no team-alpha memory** for team-beta query.
- Review-run records contain provenance, actual `atlas_vector` retrieval mode, and exact plan hash.
- Lifecycle edits update eligibility and preserve immutable history.

Mark integration tests (e.g. `@pytest.mark.atlas`) and skip only when Atlas credentials are missing; do not silently skip in the required live hackathon demonstration. Unit tests use an explicit in-memory stub and must never imply they prove Atlas integration.

## 15. Configuration and security

`.env.example` (no real secrets):

```dotenv
MONGODB_URI=mongodb+srv://<username>:<password>@<cluster-host>/?retryWrites=true&w=majority
MONGODB_DB=reviewloop_demo
MONGODB_VECTOR_INDEX=memory_vector_index
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
LLM_PROVIDER=template
# Optional if AI drafting is implemented:
# LLM_MODEL=<supported-model-name>
```

- Do **not** put real passwords in this sample or the repository.
- `.gitignore` should include `.env`, `.env.*` except `.env.example`, `.venv/`, `__pycache__/`, `.pytest_cache/`, credentials files.
- Validate data using Pydantic. Never print MongoDB URI or secret keys in errors.
- Use least-privilege Atlas credentials; sandbox network access should be restricted where practical.
- Avoid using user-provided `--team` as an access-control authority.
- Atlas isn't an authorization server: enforce scope in app logic and database query, and document the simulated-auth limitation.
- Consistent timestamps: UTC BSON datetimes in MongoDB.
- Don't record LLM-generated or memory-provided instructions as automatically approved organizational policy.

## 16. Recommended build order (Claude Code should execute this sequentially)

**Milestone 1 — Repository and fixture**
- Inspect `pwd`, `git status`, project tree, and existing files. Do not overwrite teammates' work.
- Create package structure, requirements, `.gitignore`, `.env.example`, README, ticket and frozen plan fixtures.
- Implement models, actor resolver, canonical-plan hashing, baseline review, and deterministic tests.
- Confirm Stage A returns `READY FOR REVIEW` using an explicit test stub.

**Milestone 2 — MongoDB and memory lifecycle**
- Implement PyMongo repo, indexes and health checks, seed, explicit reviewer approval, revision correction, retirement, expiry checks, and audit events.
- Make lifecycle methods idempotent or have safe concurrency checks.
- Write authorization and lifecycle tests.

**Milestone 3 — Vector Search**
- Implement embedding adapter, index creation instructions, `$vectorSearch` query with team/service/status approval/latest filters, post-filtering, and traceable results.
- Validate the Atlas index and embedding dimensions. Make errors actionable.
- Demonstrate real Atlas search returns approved alpha memory and excludes beta memory.

**Milestone 4 — Review logic and CLI**
- Add typed memory rule validator, finding provenance, `review_runs` persistence, review and memory CLI commands.
- Check the three verdicts against same hash; save trace IDs.

**Milestone 5 — Optional AI draft generation**
- Add a small draft command with a clearly labeled template mode and optional LLM mode. Do not allow probabilistic drafting to modify the frozen demo fixture. Missing input details must be flagged, not guessed.

**Milestone 6 — Polish and team handoff**
- Run `pytest -q`, show tests run, and fix failures.
- Run live Atlas demo; record actual results, never fabricated outputs.
- Add README quickstart, exact commands, expected output, troubleshooting, and concise demo talk track.
- Commit logically on a feature branch and avoid force-pushing. Don't commit `.env`.

## 17. Definition of done (acceptance checklist)

- [ ] Python CLI runs on macOS in a virtual environment.
- [ ] Fixture plan contains all three sections and passes baseline.
- [ ] Reviewer approves a memory which is really stored in MongoDB Atlas, with embedding, service/team scope, source ticket and revision.
- [ ] Atlas Vector Search actually retrieves the approved memory for alpha with metadata filtering.
- [ ] Identical input SHA-256 across all three reviews.
- [ ] Verdict sequence is exactly `READY FOR REVIEW`, `NEEDS CHANGES`, `READY FOR REVIEW`.
- [ ] Second verdict produces a concrete missing downstream reporting smoke-test finding and cites `MEM-001@1` and `INC-DBX-017`.
- [ ] Team-beta cannot see/apply team-alpha memory.
- [ ] Approved memory can be corrected and retired; superseded/expired memories are ignored.
- [ ] Every review and memory change creates auditable records.
- [ ] Tests include baseline, lifecycle, isolation and real Atlas integration checks.
- [ ] README explains setup, credentials, run sequence, limitations, and how judges can repeat the demo.
- [ ] No live deployment, Databricks integration, production auth, or autonomous execution is claimed.

## 18. Judge-facing three-minute script

1. **Problem (20s):** Teams lose deployment knowledge in past reviewer comments; the same mistake can recur.
2. **First review (30s):** Show unchanged Databricks notebook plan; baseline passes and the tool says `READY FOR REVIEW`.
3. **Human feedback (35s):** Explain synthetic prior downstream reporting outage; reviewer approves `MEM-001@1` and show it stored in Atlas.
4. **Repeat review (50s):** Re-run **same plan hash**; Atlas retrieves memory; verdict becomes `NEEDS CHANGES` with a concrete reporting-job smoke test and exact memory revision.
5. **Governance (30s):** Re-run as a different team's developer; memory excluded and verdict returns to baseline. Optionally show correction/retirement and audit history.
6. **Close (15s):** This isn't merely an AI-written checklist: it is scoped organizational memory with reviewable provenance and demonstrable behavioral impact.

## 19. What Claude Code should do immediately

1. Read this specification end to end, then inspect the current working directory and Git status.
2. Summarize the smallest runnable architecture in 5–8 sentences and list any missing **external credentials or cluster prerequisites**. **Do not delay implementation for optional choices**: use Python 3.11+, PyMongo, Pydantic, argparse or Typer, an explicit template drafting fallback, and OpenAI embeddings if keys are available.
3. Implement Milestone 1 completely and run tests before proceeding.
4. Implement Milestones 2–4. Prefer direct PyMongo over unnecessary frameworks.
5. Run the unit tests and, if Atlas credentials/index are configured, the Atlas integration tests and live demo. Clearly distinguish verified success from steps that could not run.
6. Provide the next exact terminal commands and a list of files changed. Do not ask for any secrets in chat; instruct me to put them in local `.env`.
7. Do not autonomously run destructive commands against unrelated user data or push to GitHub without an explicit request.

**End of spec.**
