# Agents-in-Context-Toronto-a-MongoDB-Hackathon

Our track of choice is:\
**1. Agents You Can Audit**\
Build an agent using MongoDB Atlas as the identity, policy and audit layer (collections, schema validation, change streams) that takes real actions across systems, where every action is tied to an identity, checked against policy before it runs, and recorded so a reviewer can reconstruct it afterward.

- How do you decide what an agent may do and enforce it *before* the action, not after?
- How do you record what the agent did, why, and under whose authority?
- How do you stop or roll back an agent mid-run?

# Idea
Build an agent which will write change tickets and document the changes made to the code base
It will take as input the git repo, where the agent will then run the code changes and then 

---

# ReviewLoop (MVP in progress)

Governed change-ticket reviewer. Authoritative spec: `CLAUDE_CODE_PROJECT_SPEC_MONGODB.md`.

**Scenario (synthetic, nothing is executed):** a MongoDB daily-revenue aggregation pipeline update
`pipeline-v1 -> pipeline-v2` (service `mongodb-revenue-aggregation`, fixture `MDB-CHG-001`,
dependency `revenue_dashboard_refresh_job`). The frozen plan passes the baseline rubric but omits
a downstream dashboard-refresh check. Demo: `READY FOR REVIEW` -> (alice approves `MEM-001@1`,
incident `INC-MDB-017`, rule `require_downstream_dashboard_refresh_test`) -> `NEEDS CHANGES`
(`MISSING_DOWNSTREAM_DASHBOARD_REFRESH_TEST`) -> team-beta (bob) `READY FOR REVIEW`.

**Status:** Milestone 1 only (fixtures, models, simulated identities, plan hashing, baseline rubric,
pure typed-rule validator, unit tests). Atlas, Vector Search, memory lifecycle and CLI are not built yet.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Fixtures: `fixtures/mongodb_aggregation_change.json`, `fixtures/mongodb_baseline_plan.json` (do not edit).
Identities (`alice`, `bob`, `beth`) are simulated, not real authentication.
