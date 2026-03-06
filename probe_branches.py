"""
probe_branches.py — Finds the correct branches API endpoint.
Run: python3 probe_branches.py
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
AUTH        = HTTPDigestAuth(PUBLIC_KEY, PRIVATE_KEY)
BASE        = "https://api.tidbcloud.com/api/v1beta"

print(f"PROJECT_ID : {PROJECT_ID}")
print(f"CLUSTER_ID : {CLUSTER_ID}")
print()

# First, confirm the cluster ID by listing clusters
print("▶ Listing clusters to confirm CLUSTER_ID...")
r = requests.get(f"{BASE}/projects/{PROJECT_ID}/clusters", auth=AUTH)
if r.status_code == 200:
    clusters = r.json().get("items", [])
    for c in clusters:
        marker = " ◀ matches .env" if str(c.get("id")) == str(CLUSTER_ID) else " ← USE THIS"
        print(f"   • name={c.get('name')}  id={c.get('id')}  type={c.get('clusterType')}{marker}")
    cluster_ids = [str(c.get("id")) for c in clusters]
else:
    print(f"  ❌ {r.status_code}: {r.text}")
    cluster_ids = [CLUSTER_ID]

print()

# Probe different branches URL patterns
candidates = [
    f"{BASE}/projects/{PROJECT_ID}/clusters/{CLUSTER_ID}/branches",
    f"https://api.tidbcloud.com/api/v1beta1/projects/{PROJECT_ID}/clusters/{CLUSTER_ID}/branches",
    f"{BASE}/clusters/{CLUSTER_ID}/branches",
    f"{BASE}/projects/{PROJECT_ID}/branches",
]

# Also try with each actual cluster ID found
for cid in cluster_ids:
    if cid != CLUSTER_ID:
        candidates.append(f"{BASE}/projects/{PROJECT_ID}/clusters/{cid}/branches")

print("▶ Probing branches endpoints...")
for url in candidates:
    r = requests.get(url, auth=AUTH, timeout=10)
    icon = "✅" if r.status_code == 200 else "❌"
    path = url.replace("https://api.tidbcloud.com", "")
    print(f"  {icon} [{r.status_code}] {path}")
    if r.status_code == 200:
        data = r.json()
        branches = data.get("branches", data.get("items", []))
        print(f"       → {len(branches)} branch(es)")
        print(f"\n✅ Working URL: {url}\n")
    else:
        print(f"       → {r.text[:80]}")
