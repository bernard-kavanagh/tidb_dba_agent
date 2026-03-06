"""
probe_api.py — Probes multiple TiDB Cloud API versions/auth combos to find what works.
Run: python3 probe_api.py
"""
import os
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

PUBLIC_KEY  = os.getenv("TIDB_CLOUD_PUBLIC_KEY")
PRIVATE_KEY = os.getenv("TIDB_CLOUD_PRIVATE_KEY")

print("=" * 65)
print("TiDB Cloud API Probe — finding working version + auth combo")
print("=" * 65)

candidates = [
    # (label, url, auth, extra_headers)
    ("v1beta1 + Digest",        "https://api.tidbcloud.com/api/v1beta1/projects", HTTPDigestAuth(PUBLIC_KEY, PRIVATE_KEY), {}),
    ("v1beta  + Digest",        "https://api.tidbcloud.com/api/v1beta/projects",  HTTPDigestAuth(PUBLIC_KEY, PRIVATE_KEY), {}),
    ("v1      + Digest",        "https://api.tidbcloud.com/api/v1/projects",      HTTPDigestAuth(PUBLIC_KEY, PRIVATE_KEY), {}),
    ("v1beta1 + Basic",         "https://api.tidbcloud.com/api/v1beta1/projects", HTTPBasicAuth(PUBLIC_KEY, PRIVATE_KEY),  {}),
    ("v1beta1 + Bearer(priv)",  "https://api.tidbcloud.com/api/v1beta1/projects", None, {"Authorization": f"Bearer {PRIVATE_KEY}"}),
    ("v1beta1 + Bearer(pub)",   "https://api.tidbcloud.com/api/v1beta1/projects", None, {"Authorization": f"Bearer {PUBLIC_KEY}"}),
    # No /api/ prefix variants
    ("v1beta1 (no /api/) + Digest", "https://api.tidbcloud.com/v1beta1/projects", HTTPDigestAuth(PUBLIC_KEY, PRIVATE_KEY), {}),
]

print()
for label, url, auth, headers in candidates:
    try:
        r = requests.get(url, auth=auth, headers=headers, timeout=10)
        snippet = r.text[:120].replace("\n", " ")
        status_icon = "✅" if r.status_code == 200 else "❌"
        print(f"  {status_icon} [{r.status_code}] {label}")
        if r.status_code == 200:
            import json
            data = r.json()
            items = data.get("items", data.get("projects", []))
            print(f"       → {len(items)} project(s) found")
            for p in items:
                print(f"          • {p.get('name', '?')} id={p.get('id', '?')}")
        else:
            print(f"       → {snippet}")
    except Exception as e:
        print(f"  ⚠️  [ERR] {label} → {e}")
    print()
