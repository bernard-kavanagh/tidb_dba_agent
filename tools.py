"""
DBA Agent Tools
---------------
All DBA capabilities exposed as LangChain @tool callables.
Imported by agent.py and bound to the LangGraph ReAct agent.

Available tools:
  - explain_query          → EXPLAIN ANALYZE on production (read-only)
  - run_query_on_branch    → SELECT on a live branch connection
  - apply_ddl_on_branch    → CREATE INDEX / ALTER TABLE on branch only
  - create_branch          → Spin up a TiDB Cloud branch
  - list_branches          → Show all branches with name, ID, and state
  - delete_branch          → Tear down a TiDB Cloud branch by ID
  - delete_branch_by_name  → Tear down a TiDB Cloud branch by display name
  - recall_memory          → Semantic search for past fixes
  - save_memory            → Persist a resolved incident
"""

import json
from langchain_core.tools import tool
from db_manager import db_manager
from branch_manager import TiDBBranchManager
from memory import dba_memory

_branch_manager = TiDBBranchManager()


# ── Read-only diagnostics ─────────────────────────────────────────────────────

@tool
def explain_query(sql: str) -> str:
    """
    Runs EXPLAIN ANALYZE on the given SQL query against the PRODUCTION database.
    Use this to diagnose slow queries — it returns the execution plan,
    actual execution time in milliseconds, and whether an index is being used.
    Never modifies any data.

    Args:
        sql: A valid SELECT statement to analyse.

    Returns:
        JSON string with keys: execution_time_ms, uses_index, plan_text.
    """
    result = db_manager.run_explain(sql)
    if "error" in result:
        return json.dumps({"error": result["error"]})
    return json.dumps({
        "execution_time_ms": result["execution_time_ms"],
        "uses_index": result["uses_index"],
        "plan_text": result["plan_text"][:2000],  # trim for context window
    })


@tool
def run_query_on_branch(sql: str, host: str, port: int, user: str, password: str) -> str:
    """
    Executes a SELECT query on a specific TiDB branch (not production).
    Use this to measure query performance AFTER applying a fix on the branch
    so you can compare before/after execution times.

    Args:
        sql: A SELECT statement to run.
        host: Branch endpoint hostname.
        port: Branch endpoint port.
        user: Branch database user.
        password: Branch database password.

    Returns:
        JSON with keys: execution_time_ms, uses_index, plan_text.
    """
    try:
        conn = db_manager.get_branch_connection(host=host, port=port, user=user, password=password)
        result = db_manager.run_explain(sql, connection=conn)
        if "error" in result:
            return json.dumps({"error": result["error"]})
        return json.dumps({
            "execution_time_ms": result["execution_time_ms"],
            "uses_index": result["uses_index"],
            "plan_text": result["plan_text"][:2000],
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Branch DDL (safe mutations only) ─────────────────────────────────────────

@tool
def apply_ddl_on_branch(ddl: str, host: str, port: int, user: str, password: str) -> str:
    """
    Applies a DDL statement (CREATE INDEX, ALTER TABLE, etc.) on a BRANCH only.
    This tool is BLOCKED from running on production — it is exclusively for
    the safety sandbox workflow.

    Args:
        ddl: A DDL statement, e.g. "CREATE INDEX idx_orders_status ON orders(status)".
        host: Branch endpoint hostname.
        port: Branch endpoint port.
        user: Branch database user.
        password: Branch database password.

    Returns:
        JSON with keys: success (bool), message.
    """
    # Safety guard: refuse if the user matches the production user.
    # On TiDB Starter, branches share the same gateway hostname as production
    # but always get a distinct userPrefix from the TiDB Cloud API — so the
    # user is the correct differentiator, not the host.
    import os
    prod_user = os.getenv("TIDB_USER", "")
    if user == prod_user:
        return json.dumps({
            "success": False,
            "message": "🚫 SAFETY VIOLATION: Refusing to run DDL on the production endpoint. Use a branch.",
        })

    # Block destructive statements
    ddl_upper = ddl.strip().upper()
    for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
        if forbidden in ddl_upper:
            return json.dumps({
                "success": False,
                "message": f"🚫 Forbidden operation '{forbidden}' blocked by safety policy.",
            })

    try:
        conn = db_manager.get_branch_connection(host=host, port=port, user=user, password=password)
        db_manager.execute(ddl, connection=conn, fetch_all=False)
        return json.dumps({"success": True, "message": f"DDL applied on branch: {ddl[:120]}"})
    except Exception as e:
        return json.dumps({"success": False, "message": str(e)})


# ── Branch lifecycle ──────────────────────────────────────────────────────────

@tool
def create_branch(branch_name: str) -> str:
    """
    Creates a new TiDB Cloud database branch — a copy-on-write snapshot of
    production. Use this before applying any experimental DDL or schema change.
    Name the branch descriptively, e.g. 'fix-orders-slow-query-101'.

    Args:
        branch_name: A short, descriptive name for the branch (no spaces).

    Returns:
        JSON with keys: branch_id, host, port, user, password, status.
    """
    try:
        info = _branch_manager.create_branch(branch_name)
        return json.dumps(info)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def list_branches() -> str:
    """
    Lists all TiDB Cloud branches on the cluster, including their name, ID,
    and current state. Use this to see what branches exist before deleting,
    or to audit branch hygiene after an investigation.

    Returns:
        JSON list of branches with keys: branch_id, name, state, created_at.
    """
    try:
        branches = _branch_manager.list_branches()
        if not branches:
            return json.dumps({"message": "No branches found.", "branches": []})
        return json.dumps({"count": len(branches), "branches": branches})
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def delete_branch(branch_id: str) -> str:
    """
    Deletes a TiDB Cloud branch by its ID. Call this after your investigation
    is complete — whether the fix is approved for production or discarded.

    Args:
        branch_id: The branch ID returned by create_branch or list_branches.

    Returns:
        JSON with keys: success (bool), message.
    """
    try:
        ok = _branch_manager.delete_branch(branch_id)
        return json.dumps({"success": ok, "message": f"Branch {branch_id} deleted." if ok else "Deletion failed."})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@tool
def delete_branch_by_name(branch_name: str) -> str:
    """
    Deletes a TiDB Cloud branch by its display name — useful when you know the
    name but not the internal ID (e.g. from the TiDB Cloud UI). Looks up the
    branch ID automatically, then deletes it. Fails safely if the name is
    ambiguous or not found.

    Args:
        branch_name: The exact display name of the branch, e.g. 'fix-orders-slow-query-101'.

    Returns:
        JSON with keys: success (bool), message, branch_id (on success).
    """
    try:
        result = _branch_manager.delete_branch_by_name(branch_name)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Hotspot detection ─────────────────────────────────────────────────────────

@tool
def check_write_hotspots() -> str:
    """
    Scans all user tables for write hotspot risks:
      1. AUTO_INCREMENT primary keys — all inserts hit the same TiKV region leader.
         Fix: switch to AUTO_RANDOM or UUID.
      2. Monotonically increasing indexed columns (created_at, updated_at, etc.) —
         index region splits lag behind write throughput, causing an index hotspot.

    Returns:
        JSON with keys: severity, auto_increment_pks, monotonic_indexes, summary, fix.
    """
    ai_query = """
        SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS c
        WHERE c.EXTRA LIKE '%auto_increment%'
          AND c.COLUMN_KEY = 'PRI'
          AND c.TABLE_SCHEMA NOT IN (
              'information_schema', 'mysql', 'performance_schema',
              'sys', 'metrics_schema'
          )
        ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME
    """
    mono_query = """
        SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, COLUMN_NAME
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE COLUMN_NAME IN (
            'created_at', 'updated_at', 'timestamp', 'create_time',
            'update_time', 'event_time', 'inserted_at', 'created_date'
        )
          AND TABLE_SCHEMA NOT IN (
              'information_schema', 'mysql', 'performance_schema',
              'sys', 'metrics_schema'
          )
          AND SEQ_IN_INDEX = 1
        ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME
    """
    ai_pks = db_manager.execute(ai_query)
    mono_indexes = db_manager.execute(mono_query)

    if isinstance(ai_pks, dict) and "error" in ai_pks:
        return json.dumps({"error": f"AUTO_INCREMENT check failed: {ai_pks['error']}"})
    if isinstance(mono_indexes, dict) and "error" in mono_indexes:
        return json.dumps({"error": f"Monotonic index check failed: {mono_indexes['error']}"})

    ai_pks = ai_pks or []
    mono_indexes = mono_indexes or []
    severity = "HIGH" if ai_pks else ("MEDIUM" if mono_indexes else "LOW")

    return json.dumps({
        "severity": severity,
        "auto_increment_pks": ai_pks,
        "monotonic_indexes": mono_indexes,
        "summary": (
            f"Found {len(ai_pks)} table(s) with AUTO_INCREMENT PK (HIGH — write hotspot). "
            f"Found {len(mono_indexes)} monotonically increasing indexed column(s) (MEDIUM — index hotspot)."
        ),
        "fix": "Replace AUTO_INCREMENT with AUTO_RANDOM to distribute writes evenly across TiKV regions.",
    })


@tool
def check_table_regions(table_name: str) -> str:
    """
    Inspects TiKV region distribution for a specific table using SHOW TABLE REGIONS.
    Reveals whether data is spread evenly or concentrated in a hotspot region.
    A single region holding >80% of writes is a clear hotspot signal.

    Args:
        table_name: Name of the table to inspect, e.g. 'orders'.

    Returns:
        JSON with keys: table, region_count, hotspot_detected, total_written_bytes,
        regions (list), summary.
    """
    # Basic validation — table names are identifiers, not user-supplied strings
    safe_name = "".join(c for c in table_name if c.isalnum() or c in ("_", "-"))
    if safe_name != table_name:
        return json.dumps({"error": f"Invalid table name: '{table_name}'"})

    rows = db_manager.execute(f"SHOW TABLE `{safe_name}` REGIONS")

    if isinstance(rows, dict) and "error" in rows:
        return json.dumps({"error": rows["error"]})
    if not rows:
        return json.dumps({"error": f"No region data returned for table '{table_name}'"})

    total_written = sum(r.get("WRITTEN_BYTES", 0) for r in rows)
    max_written = max((r.get("WRITTEN_BYTES", 0) for r in rows), default=0)
    hotspot_detected = (
        len(rows) > 1
        and total_written > 0
        and (max_written / total_written) > 0.8
    )

    regions = [
        {
            "region_id": r.get("REGION_ID"),
            "leader_store_id": r.get("LEADER_STORE_ID"),
            "written_bytes": r.get("WRITTEN_BYTES", 0),
            "read_bytes": r.get("READ_BYTES", 0),
            # Column name varies slightly across TiDB versions
            "approximate_size_mb": r.get("APPROXIMATE_SIZE(MB)", r.get("APPROXIMATE_SIZE", 0)),
            "approximate_keys": r.get("APPROXIMATE_KEYS", 0),
        }
        for r in rows
    ]

    return json.dumps({
        "table": table_name,
        "region_count": len(rows),
        "hotspot_detected": hotspot_detected,
        "total_written_bytes": total_written,
        "regions": regions[:30],  # trim for context window
        "summary": (
            f"Table '{table_name}' has {len(rows)} region(s). "
            + ("⚠️ Hotspot detected — one region holds >80% of total writes."
               if hotspot_detected
               else "✅ Write distribution looks even across regions.")
        ),
    })


@tool
def check_slow_queries(min_seconds: float = 1.0, limit: int = 10) -> str:
    """
    Queries the TiDB slow query log for recent queries exceeding the time threshold.
    Identifies which queries and tables are causing the most latency right now.

    Args:
        min_seconds: Minimum query time in seconds to include (default: 1.0).
        limit: Maximum number of results to return (default: 10).

    Returns:
        JSON with keys: count, threshold_seconds, slow_queries (list with
        query_time_s, db, query, rows_examined, index_names, user, start_time).
    """
    rows = db_manager.execute(f"""
        SELECT
            Query_time,
            DB,
            LEFT(Query, 300) AS Query,
            Rows_examined,
            Index_names,
            User,
            Start_time
        FROM INFORMATION_SCHEMA.SLOW_QUERY
        WHERE Query_time >= {float(min_seconds)}
          AND Is_internal = 0
        ORDER BY Query_time DESC
        LIMIT {int(limit)}
    """)

    if isinstance(rows, dict) and "error" in rows:
        return json.dumps({"error": rows["error"]})

    if not rows:
        return json.dumps({
            "message": f"No slow queries found exceeding {min_seconds}s.",
            "slow_queries": [],
        })

    return json.dumps({
        "count": len(rows),
        "threshold_seconds": min_seconds,
        "slow_queries": [
            {
                "query_time_s": float(r.get("Query_time", 0)),
                "db": r.get("DB", ""),
                "query": r.get("Query", ""),
                "rows_examined": r.get("Rows_examined", 0),
                "index_names": r.get("Index_names", ""),
                "user": r.get("User", ""),
                "start_time": str(r.get("Start_time", "")),
            }
            for r in rows
        ],
    }, default=str)


# ── Episodic memory ───────────────────────────────────────────────────────────

@tool
def recall_memory(error_description: str) -> str:
    """
    Searches the DBA episodic memory for past incidents that are semantically
    similar to the current issue. ALWAYS call this first during triage —
    if a fix has been proven before, reuse it rather than reinventing the wheel.

    Args:
        error_description: Natural language description of the current problem,
                           e.g. "slow query on orders table filtering by status".

    Returns:
        JSON list of past incidents with keys: incident_summary, resolution_sql,
        resolution_type, success_rating, confidence, before_time_ms, after_time_ms.
    """
    results = dba_memory.recall(error_description)
    if not results:
        return json.dumps({"message": "No similar past incidents found. Proceed with fresh analysis."})
    return json.dumps(results, default=str)


@tool
def save_memory(
    incident_summary: str,
    resolution_sql: str,
    resolution_type: str,
    resolution_description: str,
    before_time_ms: int,
    after_time_ms: int,
    table_affected: str,
    success_rating: float,
) -> str:
    """
    Saves a completed incident resolution to the DBA episodic memory vector store.
    Call this AFTER a fix has been verified on the branch and approved for production.
    This builds the agent's institutional knowledge over time.

    Args:
        incident_summary: Human-readable summary of the problem.
        resolution_sql: The DDL or SQL that fixed the issue.
        resolution_type: Category, e.g. INDEX_ADD, SCHEMA_CHANGE, QUERY_REWRITE.
        resolution_description: Explanation of why this fix works.
        before_time_ms: Query execution time before the fix (ms).
        after_time_ms: Query execution time after the fix (ms).
        table_affected: The primary table involved.
        success_rating: Float 0–1 indicating how well the fix worked (1.0 = perfect).

    Returns:
        JSON with keys: success (bool), message.
    """
    ok = dba_memory.save(
        incident_summary=incident_summary,
        resolution_sql=resolution_sql,
        resolution_type=resolution_type,
        resolution_description=resolution_description,
        success_rating=success_rating,
        before_time_ms=before_time_ms,
        after_time_ms=after_time_ms,
        table_affected=table_affected,
    )
    return json.dumps({
        "success": ok,
        "message": "Memory saved." if ok else "Failed to save memory.",
    })


# ── Exported list (consumed by agent.py) ─────────────────────────────────────

ALL_TOOLS = [
    explain_query,
    run_query_on_branch,
    apply_ddl_on_branch,
    create_branch,
    list_branches,
    delete_branch,
    delete_branch_by_name,
    check_write_hotspots,
    check_table_regions,
    check_slow_queries,
    recall_memory,
    save_memory,
]
