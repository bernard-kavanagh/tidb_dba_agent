"""
DBA Agent — LangGraph + Streamlit  (LLM-agnostic)
--------------------------------------------------
Self-Healing Database Substrate powered by:
  • Pluggable LLM backend — Claude (Anthropic), GPT-4o (OpenAI), or Gemini (Google)
  • LangGraph create_react_agent for the tool-calling loop
  • TiDBVectorStore (via memory.py) for episodic recall
  • TiDB Cloud Branching (via tools.py) for safe DDL sandboxing

Set LLM_PROVIDER in .env to switch models:
  LLM_PROVIDER=anthropic   →  ChatAnthropic  (default)
  LLM_PROVIDER=openai      →  ChatOpenAI
  LLM_PROVIDER=gemini      →  ChatGoogleGenerativeAI

Run:  streamlit run agent.py
"""

import os
import json
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path
from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from tools import ALL_TOOLS
from db_manager import db_manager

load_dotenv()

# ── Autonomous health-check prompt ────────────────────────────────────────────
HEALTH_CHECK_PROMPT = """\
Perform a full autonomous database health check. Work through each step \
independently — do not wait for further instructions.

**Step 1 — Memory scan**
Search your episodic memory for any known past incidents with this database.

**Step 2 — Query diagnostics**
Run EXPLAIN ANALYZE on each of the following known hotspot queries and record \
the execution time and whether an index is used:

1. `SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at DESC LIMIT 100`
2. `SELECT o.order_id, o.status, oi.product_id, oi.quantity FROM orders o \
JOIN order_items oi ON o.order_id = oi.order_id WHERE o.user_id = 42`
3. `SELECT user_id, event_type, COUNT(*) AS cnt FROM events \
WHERE user_id = 42 GROUP BY user_id, event_type`
4. `SELECT * FROM users WHERE country = 'IE' AND tier = 'enterprise' AND is_active = 1`
5. `SELECT product_id, SUM(quantity) AS total_sold FROM order_items \
GROUP BY product_id ORDER BY total_sold DESC LIMIT 20`

**Step 3 — Findings report**
Produce a prioritised report with severity (HIGH / MEDIUM / LOW), table \
affected, root cause, and recommended fix for each issue found.

This is a **read-only diagnostic pass** — do not create branches or apply \
fixes yet. Identify and report only.\
"""

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Safety-First DBA Agent",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

  html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

  /* Dark background */
  .stApp { background: #0d1117; color: #c9d1d9; }

  /* Sidebar */
  [data-testid="stSidebar"] {
    background: #161b22;
    border-right: 1px solid #30363d;
  }

  /* Chat containers */
  .user-bubble {
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 12px 16px;
    margin: 8px 0;
    color: #c9d1d9;
  }
  .agent-bubble {
    background: #162032;
    border: 1px solid #1f6feb;
    border-radius: 12px;
    padding: 12px 16px;
    margin: 8px 0;
    color: #c9d1d9;
  }

  /* Tool-call expander */
  .tool-expander {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 8px 12px;
    margin: 4px 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    color: #8b949e;
  }

  /* Metrics */
  [data-testid="metric-container"] {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 12px;
  }

  /* Input */
  .stTextInput > div > div > input,
  .stChatInput > div { background: #161b22 !important; color: #c9d1d9 !important; }

  /* Buttons */
  .stButton > button {
    background: #1f6feb;
    color: white;
    border: none;
    border-radius: 8px;
    font-weight: 500;
  }
  .stButton > button:hover { background: #388bfd; }

  /* Status badges */
  .badge-safe  { background: #1a3a2a; color: #3fb950; border: 1px solid #238636;
                 padding: 2px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
  .badge-warn  { background: #3a2a1a; color: #d29922; border: 1px solid #9e6a03;
                 padding: 2px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
  .badge-error { background: #3a1a1a; color: #f85149; border: 1px solid #da3633;
                 padding: 2px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
</style>
""", unsafe_allow_html=True)


# ── LLM factory ───────────────────────────────────────────────────────────────

def _build_llm():
    """
    Returns a LangChain chat model based on LLM_PROVIDER in .env.

    Supported providers:
        anthropic  →  ChatAnthropic        (default)
        openai     →  ChatOpenAI
        gemini     →  ChatGoogleGenerativeAI
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"),
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            temperature=0,
        )

    elif provider == "openai":
        # pip install langchain-openai
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(
                "langchain-openai is required for LLM_PROVIDER=openai. "
                "Run: pip install langchain-openai"
            )
        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0,
        )

    elif provider == "gemini":
        # pip install langchain-google-genai
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError(
                "langchain-google-genai is required for LLM_PROVIDER=gemini. "
                "Run: pip install langchain-google-genai"
            )
        return ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-1.5-pro"),
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            temperature=0,
        )

    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER='{provider}'. "
            "Choose one of: anthropic, openai, gemini"
        )


# ── LLM + Agent setup (cached so it doesn't reload on every rerun) ────────────

@st.cache_resource
def build_agent():
    """Initialise the LangGraph ReAct agent with the configured LLM and all DBA tools."""
    llm = _build_llm()

    system_prompt = Path("agent_context.md").read_text()

    graph = create_react_agent(
        model=llm,
        tools=ALL_TOOLS,
        prompt=system_prompt,
    )
    return graph


# ── Session state ─────────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []   # list of {"role", "content", "tool_calls"}
if "active_branch" not in st.session_state:
    st.session_state.active_branch = None  # {"name", "branch_id", "host", ...}
if "active_cluster" not in st.session_state:
    st.session_state.active_cluster = db_manager.cluster_names[0] if db_manager.cluster_names else "default"
if "run_diagnostic" not in st.session_state:
    st.session_state.run_diagnostic = False


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    provider_label = {"anthropic": "Claude", "openai": "GPT-4o", "gemini": "Gemini"}.get(provider, provider.title())
    st.markdown("## 🧬 Self-Healing Database Substrate")
    st.markdown(f"**Autonomous DBA**  \nPowered by TiDB + {provider_label}")
    st.divider()

    # Cluster selector
    st.markdown("### 🔌 Target Cluster")
    cluster_names = db_manager.cluster_names
    if len(cluster_names) > 1:
        selected_cluster = st.selectbox(
            "Active cluster",
            cluster_names,
            index=cluster_names.index(st.session_state.active_cluster)
                  if st.session_state.active_cluster in cluster_names else 0,
            label_visibility="collapsed",
        )
        if selected_cluster != st.session_state.active_cluster:
            st.session_state.active_cluster = selected_cluster
            st.rerun()
    else:
        selected_cluster = cluster_names[0] if cluster_names else "default"
        st.markdown(f"**`{selected_cluster}`**")

    db_manager.set_active_cluster(selected_cluster)
    active_host = db_manager.prod_config.get("host", "not configured")
    st.markdown(f"<small>`{active_host[:50]}`</small>", unsafe_allow_html=True)

    if st.session_state.active_branch:
        b = st.session_state.active_branch
        st.markdown(
            f'<span class="badge-warn">🔀 Branch Active</span><br>'
            f'<small><code>{b.get("branch_name", b.get("branch_id", ""))}</code></small>',
            unsafe_allow_html=True,
        )
        if st.button("🗑️ Delete Active Branch"):
            from tools import delete_branch
            result = json.loads(delete_branch.invoke({"branch_id": b["branch_id"]}))
            if result.get("success"):
                st.session_state.active_branch = None
                st.success("Branch deleted.")
                st.rerun()
            else:
                st.error(result.get("message", "Delete failed."))
    else:
        st.markdown('<span class="badge-safe">✅ No Active Branch</span>', unsafe_allow_html=True)

    st.divider()
    st.markdown("### 🔧 Tools Available")
    tool_names = {
        "🔍 explain_query":          "EXPLAIN ANALYZE on production",
        "🌿 create_branch":          "Spawn a safety sandbox",
        "📋 list_branches":          "Show all branches",
        "🗑️ delete_branch":          "Delete branch by ID",
        "🗑️ delete_branch_by_name":  "Delete branch by name",
        "⚡ apply_ddl_on_branch":    "DDL on branch only",
        "▶️ run_query_on_branch":    "Measure post-fix performance",
        "🔥 check_write_hotspots":   "Scan for AUTO_INCREMENT / monotonic index risks",
        "🗺️ check_table_regions":    "Inspect TiKV region distribution",
        "🐢 check_slow_queries":     "Query the slow query log",
        "🧠 recall_memory":          "Search past incidents",
        "💾 save_memory":            "Persist resolved fixes",
        "🗄️ show_databases":         "List all databases on the cluster",
    }
    for name, desc in tool_names.items():
        st.markdown(f"**{name}** — {desc}")

    st.divider()
    st.markdown("### 🔍 Autonomous Diagnostics")
    st.markdown(
        "Trigger a full health check — the agent will scan all tables "
        "for slow queries and missing indexes without any prompting."
    )
    if st.button("🚨 Run Health Check", use_container_width=True):
        st.session_state.run_diagnostic = True
        st.rerun()

    st.divider()
    if st.button("🧹 Clear Chat"):
        st.session_state.messages = []
        st.rerun()


# ── Header ────────────────────────────────────────────────────────────────────

st.markdown("# 🛡️ Safety-First DBA Agent")
st.markdown(
    "An autonomous DBA that **never experiments on production**. "
    "Every fix is validated in a TiDB branch before you're asked to approve it."
)
st.divider()


# ── Chat history rendering ────────────────────────────────────────────────────

_PLOTLY_LAYOUT = dict(
    plot_bgcolor="#0d1117",
    paper_bgcolor="#161b22",
    font_color="#c9d1d9",
    margin=dict(t=40, b=30, l=10, r=10),
    height=260,
)


def _render_explain_output(data: dict):
    """Rich output for explain_query / run_query_on_branch."""
    if "error" in data:
        st.error(data["error"])
        return
    ms = data.get("execution_time_ms", -1)
    uses_index = data.get("uses_index", False)
    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("Execution time", f"{ms:.1f} ms" if ms >= 0 else "n/a")
    with col_b:
        badge = "badge-safe" if uses_index else "badge-warn"
        label = "✅ Index used" if uses_index else "⚠️ Full table scan"
        st.markdown(f'<span class="{badge}">{label}</span>', unsafe_allow_html=True)
    if data.get("plan_text"):
        st.code(data["plan_text"][:1500], language="sql")


def _render_recall_output(data):
    """Render past incidents as a table if results exist."""
    if isinstance(data, dict):
        st.info(data.get("message", json.dumps(data)))
        return
    if isinstance(data, list) and data:
        cols = ["incident_summary", "resolution_type", "before_time_ms", "after_time_ms", "success_rating"]
        rows = [{c: item.get(c, "") for c in cols} for item in data]
        df = pd.DataFrame(rows)
        df.columns = ["Incident", "Type", "Before (ms)", "After (ms)", "Rating"]
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_list_branches_output(data: dict, key_prefix: str = ""):
    """Render branch list as a table with a Delete button on each row."""
    if "error" in data:
        st.error(data["error"])
        return

    branches = data.get("branches", [])
    if not branches:
        st.info("No branches found on the cluster.")
        return

    count = data.get("count", len(branches))
    st.markdown(f"**{count} branch{'es' if count != 1 else ''} on the cluster:**")

    # Header row
    h1, h2, h3, h4 = st.columns([3, 2, 3, 2])
    h1.markdown("**Name**"); h2.markdown("**State**")
    h3.markdown("**Created**"); h4.markdown("**Action**")

    for i, branch in enumerate(branches):
        bid   = branch.get("branch_id", "")
        name  = branch.get("name", "(unnamed)")
        state = branch.get("state", "")
        created = branch.get("created_at", "")[:19] if branch.get("created_at") else "—"

        c1, c2, c3, c4 = st.columns([3, 2, 3, 2])
        c1.code(name, language=None)
        badge = "badge-safe" if state in ("ACTIVE", "READY") else "badge-warn"
        c2.markdown(f'<span class="{badge}">✅ {state}</span>', unsafe_allow_html=True)
        c3.markdown(created)

        with c4:
            btn_key = f"{key_prefix}_del_{i}_{bid}"
            if st.button("🗑️ Delete", key=btn_key, type="secondary"):
                from tools import delete_branch_by_name as _del_by_name
                result = json.loads(_del_by_name.invoke({"branch_name": name}))
                if result.get("success"):
                    st.success(f"Deleted '{name}'")
                    # Clear active_branch if it was this one
                    ab = st.session_state.get("active_branch") or {}
                    if ab.get("branch_id") == bid:
                        st.session_state.active_branch = None
                    st.rerun()
                else:
                    st.error(result.get("message", "Delete failed."))


def _render_write_hotspots_output(data: dict):
    """Rich output for check_write_hotspots."""
    if "error" in data:
        st.error(data["error"])
        return

    severity = data.get("severity", "LOW")
    badge_class = {"HIGH": "badge-error", "MEDIUM": "badge-warn", "LOW": "badge-safe"}.get(severity, "badge-safe")
    st.markdown(f'<span class="{badge_class}">Severity: {severity}</span>', unsafe_allow_html=True)
    st.markdown(f"**{data.get('summary', '')}**")

    ai_pks = data.get("auto_increment_pks", [])
    if ai_pks:
        st.markdown("#### ⚠️ AUTO_INCREMENT Primary Keys (Write Hotspot Risk)")
        st.markdown(f"*Fix: {data.get('fix', '')}*")
        df = pd.DataFrame(ai_pks)
        df.columns = [c.replace("TABLE_", "").title() for c in df.columns]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.success("✅ No AUTO_INCREMENT primary keys found.")

    mono = data.get("monotonic_indexes", [])
    if mono:
        st.markdown("#### ⚠️ Monotonically Increasing Indexed Columns (Index Hotspot Risk)")
        df2 = pd.DataFrame(mono)
        df2.columns = [c.replace("TABLE_", "").title() for c in df2.columns]
        st.dataframe(df2, use_container_width=True, hide_index=True)
    else:
        st.success("✅ No monotonic index hotspot risks found.")


def _render_table_regions_output(data: dict):
    """Rich output for check_table_regions."""
    if "error" in data:
        st.error(data["error"])
        return

    hotspot = data.get("hotspot_detected", False)
    badge = "badge-error" if hotspot else "badge-safe"
    label = "⚠️ HOTSPOT DETECTED" if hotspot else "✅ No Hotspot"
    st.markdown(f'<span class="{badge}">{label}</span>', unsafe_allow_html=True)
    st.markdown(data.get("summary", ""))

    col_a, col_b = st.columns(2)
    col_a.metric("Regions", data.get("region_count", 0))
    col_b.metric("Total Written Bytes", f"{data.get('total_written_bytes', 0):,}")

    regions = data.get("regions", [])
    if regions:
        df = pd.DataFrame(regions)
        df = df.sort_values("written_bytes", ascending=False)
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_slow_queries_output(data: dict):
    """Rich output for check_slow_queries."""
    if "error" in data:
        st.error(data["error"])
        return
    if "message" in data:
        st.success(data["message"])
        return

    count = data.get("count", 0)
    threshold = data.get("threshold_seconds", 1.0)
    st.markdown(f"**{count} slow queries** exceeding `{threshold}s`:")

    rows = data.get("slow_queries", [])
    if rows:
        df = pd.DataFrame(rows)
        df = df.rename(columns={
            "query_time_s": "Time (s)",
            "db": "DB",
            "query": "Query",
            "rows_examined": "Rows Examined",
            "index_names": "Indexes",
            "user": "User",
            "start_time": "Start Time",
        })
        # Colour-code by query time
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Time (s)": st.column_config.NumberColumn(format="%.3f s"),
                "Rows Examined": st.column_config.NumberColumn(format="%d"),
                "Query": st.column_config.TextColumn(width="large"),
            },
        )


def render_tool_call(tool_name: str, tool_input: dict, tool_output: str):
    """Renders a single tool invocation as a collapsible expander."""
    icon_map = {
        "explain_query":         "🔍",
        "create_branch":         "🌿",
        "list_branches":         "📋",
        "delete_branch":         "🗑️",
        "delete_branch_by_name": "🗑️",
        "apply_ddl_on_branch":   "⚡",
        "run_query_on_branch":   "▶️",
        "check_write_hotspots":  "🔥",
        "check_table_regions":   "🗺️",
        "check_slow_queries":    "🐢",
        "recall_memory":         "🧠",
        "save_memory":           "💾",
    }
    icon = icon_map.get(tool_name, "🔧")
    # list_branches gets its own full-width expander (no side-by-side layout)
    if tool_name == "list_branches":
        with st.expander(f"{icon} `{tool_name}`", expanded=True):
            try:
                parsed = json.loads(tool_output)
                key_prefix = str(abs(hash(tool_output)))[:8]
                _render_list_branches_output(parsed, key_prefix=key_prefix)
            except Exception:
                st.code(tool_output[:1000])
        return

    with st.expander(f"{icon} `{tool_name}`", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Input**")
            st.code(json.dumps(tool_input, indent=2), language="json")
        with col2:
            st.markdown("**Output**")
            try:
                parsed = json.loads(tool_output)
                if tool_name in ("explain_query", "run_query_on_branch"):
                    _render_explain_output(parsed)
                elif tool_name == "recall_memory":
                    _render_recall_output(parsed)
                elif tool_name == "check_write_hotspots":
                    _render_write_hotspots_output(parsed)
                elif tool_name == "check_table_regions":
                    _render_table_regions_output(parsed)
                elif tool_name == "check_slow_queries":
                    _render_slow_queries_output(parsed)
                else:
                    st.code(json.dumps(parsed, indent=2), language="json")
            except Exception:
                st.code(tool_output[:1000])


def render_performance_chart(tool_calls: list[dict]):
    """Renders a before/after bar chart when both explain_query and run_query_on_branch fired."""
    before_ms = after_ms = None
    for tc in tool_calls:
        try:
            data = json.loads(tc["output"])
            ms = data.get("execution_time_ms", -1)
            if ms < 0:
                continue
            if tc["name"] == "explain_query" and before_ms is None:
                before_ms = ms
            elif tc["name"] == "run_query_on_branch" and after_ms is None:
                after_ms = ms
        except Exception:
            pass

    if before_ms is not None and after_ms is not None:
        improvement = ((before_ms - after_ms) / before_ms * 100) if before_ms > 0 else 0
        fig = go.Figure(data=[
            go.Bar(name="Before fix", x=["Query time (ms)"], y=[before_ms],
                   marker_color="#f85149", text=[f"{before_ms:.1f} ms"], textposition="outside"),
            go.Bar(name="After fix",  x=["Query time (ms)"], y=[after_ms],
                   marker_color="#3fb950", text=[f"{after_ms:.1f} ms"], textposition="outside"),
        ])
        fig.update_layout(
            title=f"Performance improvement: {improvement:.1f}% faster",
            yaxis_title="ms",
            barmode="group",
            **_PLOTLY_LAYOUT,
        )
        st.plotly_chart(fig, use_container_width=True)


def render_message(msg: dict):
    if msg["role"] == "user":
        st.markdown(f'<div class="user-bubble">👤 {msg["content"]}</div>', unsafe_allow_html=True)
    elif msg["role"] == "assistant":
        # Render any tool calls first
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            render_tool_call(tc["name"], tc["input"], tc["output"])
        render_performance_chart(tool_calls)
        # Then the assistant text
        if msg["content"]:
            st.markdown(f'<div class="agent-bubble">🤖 {msg["content"]}</div>', unsafe_allow_html=True)


for msg in st.session_state.messages:
    render_message(msg)


# ── Input + agent invocation ──────────────────────────────────────────────────

# Resolve the active prompt — either from chat input or the diagnostic button
user_prompt  = None   # actual prompt sent to the LLM
display_text = None   # shorter text shown in the chat bubble

if prompt := st.chat_input("Describe the database issue you're seeing..."):
    user_prompt  = prompt
    display_text = prompt
elif st.session_state.run_diagnostic:
    user_prompt  = HEALTH_CHECK_PROMPT
    display_text = "🔍 Running autonomous database health check..."
    st.session_state.run_diagnostic = False

if user_prompt:
    # Show user message immediately
    user_msg = {"role": "user", "content": display_text, "tool_calls": []}
    st.session_state.messages.append(user_msg)
    render_message(user_msg)

    # Build message history — use stored display text for past turns,
    # but send the full user_prompt for the current turn
    lc_messages = []
    for m in st.session_state.messages[:-1]:
        if m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant" and m.get("content"):
            lc_messages.append(AIMessage(content=m["content"]))
    lc_messages.append(HumanMessage(content=user_prompt))

    # Invoke the agent
    graph = build_agent()

    tool_calls_this_turn: list[dict] = []
    final_text = ""

    with st.spinner("🤖 Agent thinking..."):
        try:
            result = graph.invoke({"messages": lc_messages})

            # Walk the output messages to collect tool calls and final answer
            # Build a lookup from tool_call_id → entry so outputs match correctly
            id_to_tc: dict[str, dict] = {}
            for msg in result["messages"]:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    # AIMessage with tool calls — capture inputs
                    for tc in msg.tool_calls:
                        entry = {
                            "name": tc["name"],
                            "input": tc["args"],
                            "output": "",
                        }
                        tool_calls_this_turn.append(entry)
                        id_to_tc[tc["id"]] = entry
                elif isinstance(msg, ToolMessage):
                    # Match output to the correct tool call by ID
                    entry = id_to_tc.get(msg.tool_call_id)
                    if entry:
                        entry["output"] = msg.content
                    # Track active branch if create_branch was called
                    try:
                        data = json.loads(msg.content)
                        if "branch_id" in data and "host" in data:
                            st.session_state.active_branch = data
                    except Exception:
                        pass
                elif isinstance(msg, AIMessage) and msg.content:
                    final_text = msg.content

        except Exception as e:
            final_text = f"❌ Agent error: {e}"

    # Render tool calls + final answer
    for tc in tool_calls_this_turn:
        render_tool_call(tc["name"], tc["input"], tc["output"])
    render_performance_chart(tool_calls_this_turn)

    if final_text:
        st.markdown(f'<div class="agent-bubble">🤖 {final_text}</div>', unsafe_allow_html=True)

    # Persist to session state
    st.session_state.messages.append({
        "role": "assistant",
        "content": final_text,
        "tool_calls": tool_calls_this_turn,
    })

    st.rerun()
