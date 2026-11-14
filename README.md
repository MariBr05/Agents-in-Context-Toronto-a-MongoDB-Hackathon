# Agents-in-Context-Toronto-a-MongoDB-Hackathon

Our track of choice is:
**1. Agents You Can Audit**\
Build an agent using MongoDB Atlas as the identity, policy and audit layer (collections, schema validation, change streams) that takes real actions across systems, where every action is tied to an identity, checked against policy before it runs, and recorded so a reviewer can reconstruct it afterward.

- How do you decide what an agent may do and enforce it *before* the action, not after?
- How do you record what the agent did, why, and under whose authority?
- How do you stop or roll back an agent mid-run?

**Examples:** 

- A claims-handling agent with per-step policy gates and a ledger a compliance reviewer can query. 
- A KYC-refresh agent that is blocked from touching accounts outside its scope.