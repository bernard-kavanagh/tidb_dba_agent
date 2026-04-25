# Cognitive Foundation — Vocabulary

Canonical terms used across all cognitive foundation repositories. Use these consistently in READMEs, documentation, blog posts, and presentations.

## Architecture

| Term | Definition |
|---|---|
| **Cognitive Foundation** | The architectural principle: the database is the agent's cognitive substrate, not its storage layer. It maintains knowledge over time through structured memory and lifecycle management. |
| **Unified Data Substrate** | A single ACID-compliant cluster that serves as both the operational data store and the agent's memory. No separate vector store, no cache, no warehouse. One transaction boundary. |
| **Data Plane** | Domain-specific operational data: telemetry, transactions, events, and ground truth catalogs. The raw material the agent reasons against. |
| **Context Plane** | Three-tier agent memory that persists across sessions. Episodic, semantic, and procedural memory — maintained by the five custodial duties. |
| **Agent Layer** | Stateless LLM with tools. Disposable and ephemeral. The platform remembers on its behalf. |
| **Domain Adapter** | The pluggable configuration that defines a use case: schema mapping, window/aggregation config, anomaly weights, text banding rules, and seed catalog. The only part you write per domain. |

## Three-Tier Memory

| Term | Definition | Typical implementation |
|---|---|---|
| **Episodic Memory** | Time-stamped records of what happened — interactions, investigations, decisions, outcomes. The agent's experiential history. | `agent_reasoning`, `chat_history` |
| **Semantic Memory** | Learned knowledge that persists across sessions and agents. Facts, patterns, and rules extracted from experience. Scoped (global, site, model, entity). | `fleet_memory`, `sales_knowledge`, knowledge base tables |
| **Procedural Memory** | Learned workflows and execution strategies. How the agent acts on what it knows — investigation playbooks, escalation logic, remediation procedures. | Branching + RCA logic, agent directives, escalation rules |

## Five Custodial Duties

| Duty | Definition |
|---|---|
| **Write Control** | Only confirmed outcomes are persisted. Working reasoning is ephemeral. Memory grows at O(investigations), not O(reasoning steps). |
| **Deduplication** | Near-duplicate memories (cosine distance < 0.15) are merged rather than accumulated. One strong memory with high evidence count, not ten weak duplicates. |
| **Reconciliation** | New evidence that contradicts existing memory auto-supersedes the older conclusion. `superseded_by` links the chain. Truth evolves, not accumulates. |
| **Confidence Decay** | Memories that aren't reinforced lose confidence over time (e.g., 5% monthly). Below a threshold (e.g., 0.30), auto-deprecated. Stale knowledge fades rather than poisoning. |
| **Compaction** | Periodic re-clustering merges memories that have drifted close together. Evidence counts consolidated. The knowledge store stays lean. |

## Key Mechanisms

| Term | Definition |
|---|---|
| **Context Assembly** | Budget-constrained function that builds the agent's prompt from priority-ordered sources. Runs before the model is invoked. Zero LLM calls. Pure SQL. The model never decides what to remember — the platform decides for it. |
| **Hybrid Search** | Vector cosine similarity + FULLTEXT keyword matching in a single SQL query. Vectors catch meaning ("salt corrosion" ≈ "coastal earth leakage"). Keywords catch identifiers (error codes, firmware versions). |
| **Semantic Banding** | Converting raw metrics to natural language before embedding. `voltage_stddev=12.3` → "high voltage variance, possible supply sag." Dramatically improves vector recall. |
| **Human-in-the-Loop** | The human validates, not executes. Serverless branching enables safe autonomy: agent proposes → branch validates → human approves → promote to production. |

## Problem Framing

| Term | Definition |
|---|---|
| **Memory Wall** | The infrastructure problem caused by stateless models on fragmented stacks. Not a model limitation — an architecture limitation. |
| **Token Tax** | The quadratic cost of re-assembling context from scratch on every invocation. By the tenth investigation, you've paid for the first nine ten times over. |
| **State Explosion** | The scaling problem when N users × M agents × Z branches creates thousands of concurrent memory contexts. Traditional databases assume one app, one database, predictable load. Agent workloads are the opposite. |
