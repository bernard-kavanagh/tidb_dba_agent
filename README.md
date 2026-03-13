# 🧬 Self-Healing Database Substrate

> *"The database that fixes itself — safely, transparently, and with your approval."*

An autonomous DBA agent that detects performance degradation, proposes and validates fixes in an isolated sandbox, and waits for your sign-off before touching production. Every action is explainable. Every fix is reversible. LLM-agnostic.

Built with **Claude / OpenAI / Gemini** · **LangGraph** · **Streamlit** · **TiDB Cloud** · **Plotly**

---

## What is a Self-Healing Database Substrate?

Traditional databases are passive — they accumulate technical debt silently until a human notices a slow query, a hotspot alert, or a 3am page. This project inverts that model.

The **substrate** is the combination of:
- A **live production database** (TiDB Cloud Serverless) that serves real traffic
- A **copy-on-write branching layer** (TiDB Cloud Branches) for zero-risk sandboxing — every fix is proven before it touches production
- An **LLM-powered reasoning agent** (LangGraph ReAct loop) that observes, hypothesises, and validates
- A **vector episodic memory** (TiDB Vector Store) that learns from every past incident

Together they form a system that continuously monitors itself, proposes targeted structural improvements, benchmarks them safely, and self-heals — without ever modifying production until a human says yes.

---

## How it works

**Prompted mode** — describe an issue in the chat:
1. **Triage** — you describe a slow query or performance issue
2. **Recall** — the agent searches its vector memory for similar past incidents
3. **Sandbox** — a TiDB Cloud branch (copy-on-write snapshot of production) is created with isolated credentials
4. **Fix & Verify** — the proposed DDL (`CREATE INDEX`, `ALTER TABLE`, etc.) is applied to the branch and benchmarked with `EXPLAIN ANALYZE`
5. **Report** — the agent presents before/after metrics and waits for your approval before touching production

**Autonomous mode** — click 🚨 Run Health Check in the sidebar:
The agent independently runs `EXPLAIN ANALYZE` across all known hotspot queries, scans for write hotspots and region imbalances, checks episodic memory for past incidents, and produces a prioritised findings report — no prompting required.

---

## Prerequisites

- Python 3.10+
- A [TiDB Cloud](https://tidbcloud.com) Serverless cluster
- TiDB Cloud API keys (for branching)
- An LLM API key — Anthropic, OpenAI, or Google Gemini (see [LLM Configuration](#llm-configuration))
- The ISRG Root X1 SSL certificate ([download](https://letsencrypt.org/certs/isrgrootx1.pem))

---

## Setup

### 1. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
# TiDB Cloud Serverless connection
TIDB_HOST=gateway01.<region>.prod.aws.tidbcloud.com
TIDB_PORT=4000
TIDB_USER=your_cluster_user.root
TIDB_PASSWORD=your_password
TIDB_DATABASE=dba_agent_db
TIDB_SSL_CA=/path/to/isrgrootx1.pem

# TiDB Cloud API (required for branch creation)
TIDB_CLOUD_PUBLIC_KEY=your_public_key
TIDB_CLOUD_PRIVATE_KEY=your_private_key
TIDB_CLOUD_PROJECT_ID=your_project_id
TIDB_CLOUD_CLUSTER_ID=your_cluster_id

# ── LLM Provider (pick one) ──────────────────────────────────────
LLM_PROVIDER=anthropic           # anthropic | openai | gemini

# Anthropic Claude (default)
ANTHROPIC_API_KEY=your-anthropic-api-key
CLAUDE_MODEL=claude-sonnet-4-5

# OpenAI — set LLM_PROVIDER=openai to activate
# OPENAI_API_KEY=your-openai-api-key
# OPENAI_MODEL=gpt-4o

# Google Gemini — set LLM_PROVIDER=gemini to activate
# GOOGLE_API_KEY=your-google-api-key
# GEMINI_MODEL=gemini-1.5-pro

# Embedding model (local, no API key needed)
EMBEDDING_MODEL=all-MiniLM-L6-v2
EMBEDDING_DIM=384
```

### 3. Initialise the database

First load your `.env` vars into the shell, then apply the schema and seed:

```bash
# Load env vars into shell
set -a && source .env && set +a

# Apply schema (creates dba_agent_db — will not touch existing databases)
mysql -h $TIDB_HOST -P $TIDB_PORT -u $TIDB_USER -p --ssl-ca=$TIDB_SSL_CA < schema.sql

# Seed demo data
python3 seed_data.py
```

The seeder generates realistic production-like data:

| Table | Volume | Realism features |
|---|---|---|
| `users` | 10,000 | 3-year signup history, tier distribution |
| `products` | 500 | Multiple categories and brands |
| `orders` | 50,000 | Growth-weighted dates, power user concentration |
| `order_items` | ~96,000 | Variable line items per order |
| `reviews` | 10,000 | Skewed rating distribution |
| `events` | 200,000 | Session-based, business-hours weighted, funnel sequencing |
| `support_tickets` | 2,500 | Realistic priority and status distribution |
| `dba_episodic_memory` | 22 | Pre-seeded incidents spanning 8 resolution types |

**Power user model:** 15% of users generate 60% of orders and events, mirroring a real Pareto distribution.

### 4. Run the agent

```bash
python3 -m streamlit run agent.py
```

The UI will be available at `http://localhost:8501`.

### 5. Reset for a fresh demo run

After the agent has applied fixes to production you can wipe the database and start over:

```bash
bash reset.sh
```

One command. That's it. Here's what happens under the hood:

```
1. DROP DATABASE dba_agent_db        ← wipes everything, including the vector table
2. mysql < schema.sql                ← recreates all app tables with intentional missing indexes
3. python3 seed_data.py              ← re-seeds 10K users, 50K orders, 200K events …
                                        └─ seed_episodic_memory() fires at the end
                                             └─ TiDBVectorStore.add_texts() auto-creates
                                                dba_episodic_memory with the correct schema
                                                and writes all 22 pre-seeded incidents ✅
```

The `dba_episodic_memory` table is owned by TiDBVectorStore — it never appears in `schema.sql`. Dropping the whole database is therefore the cleanest possible reset: the vector table is recreated with the right schema automatically on first write, every single time.

---

## LLM Configuration

The agent is **LLM-agnostic**. Set `LLM_PROVIDER` in `.env` to switch models with no code changes:

| Provider | `LLM_PROVIDER` | Required env var | Default model |
|---|---|---|---|
| Anthropic Claude | `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4o` |
| Google Gemini | `gemini` | `GOOGLE_API_KEY` | `gemini-1.5-pro` |

All three providers are wired through LangChain's unified chat interface. See `agent.py` → `build_agent()` for the provider selection logic.

---

## Demo schema

The schema (`schema.sql`) mimics a real e-commerce backend with **intentional performance issues** for the agent to find and fix:

| Table | Intentional problem |
|---|---|
| `orders` | No index on `status`, `user_id`, or `created_at` |
| `order_items` | No index on `order_id` — causes N+1 full scans |
| `events` | No index on `user_id`, `event_type`, or `created_at` (high-volume table) |
| `products` | No index on `category` or `brand` |
| `reviews` | No index on `product_id` or `rating` |

The agent detects these via `EXPLAIN ANALYZE` and proposes targeted index additions.

---

## Tools

| Tool | What it does |
|---|---|
| `explain_query` | Runs `EXPLAIN ANALYZE` on production (read-only) |
| `create_branch` | Spawns a copy-on-write TiDB branch with isolated credentials |
| `list_branches` | Lists all active branches |
| `delete_branch` | Cleans up a branch by ID |
| `delete_branch_by_name` | Cleans up a branch by name |
| `apply_ddl_on_branch` | Runs `CREATE INDEX` / `ALTER TABLE` on a branch only |
| `run_query_on_branch` | Benchmarks a query on the branch post-fix |
| `check_write_hotspots` | Detects `AUTO_INCREMENT` PKs and monotonic index risks |
| `check_table_regions` | Inspects TiKV region distribution for write hotspots |
| `check_slow_queries` | Queries the TiDB slow query log |
| `recall_memory` | Semantic search over past resolved incidents |
| `save_memory` | Persists a verified fix to the vector store |

---

## Autonomous diagnostics

Click **🚨 Run Health Check** in the sidebar to trigger an unprompted sweep. The agent will:

1. Search episodic memory for known past incidents
2. Run `EXPLAIN ANALYZE` on known hotspot queries
3. Scan for write hotspots (`AUTO_INCREMENT`, monotonic indexes)
4. Check TiKV region distribution for imbalanced tables
5. Produce a prioritised report (HIGH / MEDIUM / LOW) with recommended fixes

This is a read-only pass — no branches are created until you ask the agent to apply a fix.

---

## Visualisations

Tool outputs are rendered with rich UI rather than raw JSON:

| Tool | Visualisation |
|---|---|
| `explain_query` | Execution time metric + index-used/full-scan badge + plan text |
| `run_query_on_branch` | Same as above, for the post-fix measurement |
| `recall_memory` | Past incidents as an interactive sortable table |
| `check_write_hotspots` | Severity badge + AUTO_INCREMENT PK table + monotonic index table |
| `check_table_regions` | Hotspot badge + region count + sortable regions dataframe |
| `check_slow_queries` | Slow query log table with formatted timing columns |
| `list_branches` | Branch table with per-row 🗑️ Delete button |
| Before + after in same turn | Plotly before/after bar chart with % improvement |

---

## Demo SQL queries

Run these in the TiDB Cloud SQL editor (or any MySQL client) to narrate the before/after story.

### 🟡 Before the demo

```sql
-- 1. Data volume — show the scale of the production database
SELECT
    table_name,
    table_rows                                          AS approx_rows,
    ROUND(data_length / 1024 / 1024, 2)                AS data_mb,
    ROUND(index_length / 1024 / 1024, 2)               AS index_mb
FROM information_schema.tables
WHERE table_schema = 'dba_agent_db'
  AND table_type = 'BASE TABLE'
ORDER BY table_rows DESC;
```

```sql
-- 2. The smoking gun — tables with NO secondary indexes
SELECT
    t.table_name,
    COUNT(s.index_name) - 1                            AS secondary_indexes,
    t.table_rows                                        AS approx_rows,
    CASE WHEN COUNT(s.index_name) - 1 = 0
         THEN '🔴 NO SECONDARY INDEXES'
         ELSE '✅ Indexed'
    END                                                AS status
FROM information_schema.tables t
LEFT JOIN information_schema.statistics s
       ON s.table_schema = t.table_schema
      AND s.table_name   = t.table_name
      AND s.index_name  != 'PRIMARY'
WHERE t.table_schema = 'dba_agent_db'
  AND t.table_type   = 'BASE TABLE'
  AND t.table_name NOT IN ('dba_episodic_memory','incident_log')
GROUP BY t.table_name, t.table_rows
ORDER BY secondary_indexes, t.table_rows DESC;
```

```sql
-- 3. Episodic memory — what the agent already knows (pre-seeded history)
-- (TiDBVectorStore stores structured fields as JSON inside `meta`)
SELECT
    LEFT(document, 80)                                 AS incident,
    meta->>'$.resolution_type'                         AS resolution_type,
    CAST(meta->>'$.before_time_ms' AS SIGNED)          AS before_time_ms,
    CAST(meta->>'$.after_time_ms'  AS SIGNED)          AS after_time_ms,
    ROUND(
        (meta->>'$.before_time_ms' - meta->>'$.after_time_ms')
        / NULLIF(meta->>'$.before_time_ms', 0) * 100, 0
    )                                                  AS pct_improvement,
    CAST(meta->>'$.success_rating' AS DECIMAL(3,2))    AS success_rating
FROM dba_episodic_memory
ORDER BY create_time DESC;
```

```sql
-- 4. Memory volume by type — breadth of what the agent has learned
SELECT
    meta->>'$.resolution_type'                         AS resolution_type,
    COUNT(*)                                           AS memories,
    ROUND(AVG(meta->>'$.before_time_ms'), 0)           AS avg_before_ms,
    ROUND(AVG(meta->>'$.after_time_ms'),  0)           AS avg_after_ms,
    ROUND(AVG(
        (meta->>'$.before_time_ms' - meta->>'$.after_time_ms')
        / NULLIF(meta->>'$.before_time_ms', 0) * 100
    ), 0)                                              AS avg_improvement_pct
FROM dba_episodic_memory
GROUP BY meta->>'$.resolution_type'
ORDER BY memories DESC;
```

```sql
-- 5. Prove the pain — full table scan with no index
EXPLAIN ANALYZE
SELECT order_id, status, total_amount, created_at
FROM orders
WHERE status = 'pending'
ORDER BY created_at DESC
LIMIT 20;
-- Look for: TableFullScan, rows examined >> rows returned
```

### 🟢 After the demo

```sql
-- 6. New indexes applied by the agent
SELECT
    table_name,
    index_name,
    GROUP_CONCAT(column_name ORDER BY seq_in_index)   AS columns,
    index_type
FROM information_schema.statistics
WHERE table_schema = 'dba_agent_db'
  AND index_name  != 'PRIMARY'
GROUP BY table_name, index_name, index_type
ORDER BY table_name, index_name;
```

```sql
-- 7. Same query as #5 — now with the index
EXPLAIN ANALYZE
SELECT order_id, status, total_amount, created_at
FROM orders
WHERE status = 'pending'
ORDER BY created_at DESC
LIMIT 20;
-- Look for: IndexRangeScan, rows examined ≈ rows returned
```

```sql
-- 8. New memories written during this demo session
SELECT
    LEFT(document, 80)                                 AS incident,
    meta->>'$.resolution_type'                         AS resolution_type,
    CAST(meta->>'$.before_time_ms' AS SIGNED)          AS before_time_ms,
    CAST(meta->>'$.after_time_ms'  AS SIGNED)          AS after_time_ms,
    create_time                                        AS created_at
FROM dba_episodic_memory
ORDER BY create_time DESC
LIMIT 10;
```

```sql
-- 9. The TiDB pitch — one cluster handling OLTP + vector search
SELECT
    (SELECT COUNT(*) FROM orders)                      AS orders_rows,
    (SELECT COUNT(*) FROM events)                      AS events_rows,
    (SELECT COUNT(*) FROM dba_episodic_memory)         AS vector_memories,
    (SELECT COUNT(*) FROM incident_log)                AS agent_steps_logged,
    (SELECT ROUND(AVG(meta->>'$.after_time_ms'), 1)
     FROM dba_episodic_memory
     WHERE meta->>'$.resolution_type' = 'INDEX_ADD')  AS avg_query_ms_after_fix;
-- One cluster. OLTP + HTAP + Vector search. No separate vector DB needed.
```

---

## Safety guardrails

- `DROP TABLE`, `TRUNCATE`, and `DELETE FROM` are **blocked** on all branch connections
- DDL against the production host is **refused** at the tool level by comparing user prefixes
- Branch root passwords are **generated randomly per-branch** and never reused or stored
- No fix is applied to production **without explicit user approval**
- PII is never printed to the chat window

---

## Project structure

```
dba_agent/
├── agent.py          # Streamlit UI + LangGraph agent (LLM-agnostic)
├── agent_context.md  # System prompt / agent identity
├── tools.py          # LangChain tool definitions (12 DBA tools)
├── db_manager.py     # TiDB connection + EXPLAIN utilities
├── branch_manager.py # TiDB Cloud branching API client (v1beta1)
├── memory.py         # Vector store episodic memory
├── schema.sql        # Database schema (with intentional perf issues)
├── seed_data.py      # Demo data loader
├── requirements.txt
└── .env.example
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Streamlit UI                          │
│              (agent.py — chat + rich renders)            │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              LangGraph ReAct Agent                       │
│     Claude / GPT-4o / Gemini  ←→  12 DBA Tools          │
└───────┬────────────────────────────────┬────────────────┘
        │                                │
┌───────▼───────┐              ┌─────────▼──────────────┐
│  TiDB Cloud   │              │   TiDB Cloud Branches  │
│  Production   │              │   (copy-on-write        │
│  (read-only   │              │    DDL sandbox,         │
│   explains)   │              │    per-branch password) │
└───────┬───────┘              └────────────────────────┘
        │
┌───────▼───────────────────────────────────────────────┐
│   TiDB Vector Store  (episodic memory)                │
│   Sentence-transformers embeddings (local, no API)    │
└───────────────────────────────────────────────────────┘
```

---

## Contributing

PRs welcome. The codebase is intentionally minimal — one file per concern, no frameworks beyond LangChain/LangGraph. The goal is a substrate you can clone, point at your own TiDB cluster, and have running in under 10 minutes.
