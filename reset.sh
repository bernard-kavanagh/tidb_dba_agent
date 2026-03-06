#!/usr/bin/env bash
# reset.sh — Wipe and rebuild dba_agent_db for a fresh demo run
# Usage: bash reset.sh
# (run from the project root with .env already sourced, or let the script source it)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load .env if present ────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  echo "📦 Loading .env..."
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

# ── Validate required vars ──────────────────────────────────────────────────
: "${TIDB_HOST:?TIDB_HOST is not set}"
: "${TIDB_PORT:=4000}"
: "${TIDB_USER:?TIDB_USER is not set}"
: "${TIDB_PASSWORD:?TIDB_PASSWORD is not set}"
: "${TIDB_SSL_CA:?TIDB_SSL_CA is not set}"

MYSQL_CMD="mysql -h $TIDB_HOST -P $TIDB_PORT -u $TIDB_USER -p$TIDB_PASSWORD --ssl-ca=$TIDB_SSL_CA"

# ── Step 1: Drop the database ───────────────────────────────────────────────
echo ""
echo "🗑️  Dropping dba_agent_db..."
$MYSQL_CMD -e "DROP DATABASE IF EXISTS dba_agent_db;"

# ── Step 2: Recreate schema ─────────────────────────────────────────────────
echo "📐 Applying schema..."
$MYSQL_CMD < "$SCRIPT_DIR/schema.sql"

# ── Step 3: Re-seed data ────────────────────────────────────────────────────
echo "🌱 Seeding demo data..."
cd "$SCRIPT_DIR"
python3 seed_data.py

echo ""
echo "✅ Reset complete — dba_agent_db is fresh with all intentional perf issues restored."
echo "   Run:  python3 -m streamlit run agent.py"
