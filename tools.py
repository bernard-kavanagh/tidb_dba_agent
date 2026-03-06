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
  - delete_branch          → Tear down a TiDB Cloud branch
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
def delete_branch(branch_id: str) -> str:
    """
    Deletes a TiDB Cloud branch by its ID. Call this after your investigation
    is complete — whether the fix is approved for production or discarded.

    Args:
        branch_id: The branch ID returned by create_branch.

    Returns:
        JSON with keys: success (bool), message.
    """
    try:
        ok = _branch_manager.delete_branch(branch_id)
        return json.dumps({"success": ok, "message": f"Branch {branch_id} deleted." if ok else "Deletion failed."})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


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
    delete_branch,
    recall_memory,
    save_memory,
]
