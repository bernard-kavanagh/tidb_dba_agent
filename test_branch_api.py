"""
test_branch_api.py — Diagnoses TiDB Cloud branching API connectivity.
Run: python3 test_branch_api.py
"""
import os
import requests
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv

load_dotenv()

PUBLIC_KEY  = os.getenv("TIDB_CLOUD_PUBLIC_KEY")
PRIVATE_KEY = os.getenv("TIDB_CLOUD_PRIVATE_KEY")
PROJECT_ID  = os.getenv("TIDB_CLOUD_PROJECT_ID")
CLUSTER_ID  = os.getenv("TIDB_CLOUD_CLUSTER_ID")
BASE        = "https://api.tidbcloud.com/api/v1beta1"
AUTH        = HTTPDigestAuth(PUBLIC_KEY, PRIVATE_KEY)

print("=" * 60)
print("TiDB Cloud Branching API Diagnostic")
print("=" * 60)
print(f"  PUBLIC_KEY  : {PUBLIC_KEY[:6]}...  (set={bool(PUBLIC_KEY)})")
print(f"  PROJECT_ID  : {PROJECT_ID}")
print(f"  CLUSTER_ID  : {CLUSTER_ID}")
print()

# ── Step 1: List projects (validates API keys) ───────────────────
print("▶ Step 1 — Validating API keys (GET /projects)...")
r = requests.get(f"{BASE}/projects", auth=AUTH)
if r.status_code == 200:
    projects = r.json().get("items", [])
    print(f"  ✅ API keys valid. Found {len(projects)} project(s):")
    for p in projects:
        marker = " ◀ matches .env" if p.get("id") == PROJECT_ID else ""
        print(f"     • {p.get('name')} — id={p.get('id')}{marker}")
    if not any(p.get("id") == PROJECT_ID for p in projects):
        print(f"\n  ❌ PROJECT_ID '{PROJECT_ID}' not found in your account.")
        print("     Copy the correct id from the list above into .env → TIDB_CLOUD_PROJECT_ID")
        exit(1)
elif r.status_code == 401:
    print(f"  ❌ 401 Unauthorized — API keys are wrong or expired.")
    print(f"     Response: {r.text}")
    exit(1)
else:
    print(f"  ❌ Unexpected {r.status_code}: {r.text}")
    exit(1)

# ── Step 2: List clusters in project (validates project ID) ──────
print(f"\n▶ Step 2 — Listing clusters in project {PROJECT_ID} (GET /projects/{{id}}/clusters)...")
r = requests.get(f"{BASE}/projects/{PROJECT_ID}/clusters", auth=AUTH)
if r.status_code == 200:
    clusters = r.json().get("items", [])
    print(f"  ✅ Found {len(clusters)} cluster(s):")
    for c in clusters:
        marker = " ◀ matches .env" if c.get("id") == CLUSTER_ID else ""
        print(f"     • {c.get('name')} — id={c.get('id')}  type={c.get('clusterType')}{marker}")
    if not any(c.get("id") == CLUSTER_ID for c in clusters):
        print(f"\n  ❌ CLUSTER_ID '{CLUSTER_ID}' not found in this project.")
        print("     Copy the correct id from the list above into .env → TIDB_CLOUD_CLUSTER_ID")
        exit(1)
else:
    print(f"  ❌ {r.status_code}: {r.text}")
    exit(1)

# ── Step 3: Hit the branches endpoint directly ───────────────────
print(f"\n▶ Step 3 — Hitting branches endpoint...")
branches_url = f"{BASE}/projects/{PROJECT_ID}/clusters/{CLUSTER_ID}/branches"
print(f"  URL: {branches_url}")
r = requests.get(branches_url, auth=AUTH)
if r.status_code == 200:
    branches = r.json().get("branches", r.json().get("items", []))
    print(f"  ✅ Branching API reachable. Existing branches: {len(branches)}")
    for b in branches:
        print(f"     • {b.get('displayName')} ({b.get('state')})")
    print("\n✅ All checks passed — branching should work. Restart the agent.")
elif r.status_code == 404:
    print(f"  ❌ 404 Not Found")
    print(f"     Response: {r.text}")
    print()
    print("  Possible causes:")
    print("  1. Branching is not enabled on this cluster tier.")
    print("     → TiDB Cloud Serverless supports branching; Developer/Dedicated may not.")
    print("     → Check: console.tidbcloud.com → your cluster → 'Branches' tab exists?")
    print("  2. The cluster type is 'DEVELOPER' (free tier) — branching requires 'SERVERLESS'.")
    cluster_type = next((c.get("clusterType") for c in clusters if c.get("id") == CLUSTER_ID), "?")
    print(f"     → Your cluster type: {cluster_type}")
elif r.status_code == 403:
    print(f"  ❌ 403 Forbidden — API key lacks 'branch:create' permission.")
    print(f"     Regenerate the key in TiDB Cloud → IAM → API Keys with branch permissions.")
else:
    print(f"  ❌ {r.status_code}: {r.text}")
