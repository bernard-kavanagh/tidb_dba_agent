"""
TiDB Cloud Branch Manager
--------------------------
Wraps the TiDB Cloud REST API (v1beta) to create, poll, and delete
database branches for the Safety Sandbox workflow.

Requires: TIDB_CLOUD_PUBLIC_KEY, TIDB_CLOUD_PRIVATE_KEY,
          TIDB_CLOUD_PROJECT_ID, TIDB_CLOUD_CLUSTER_ID
"""

import os
import time
import requests
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv

load_dotenv()


class TiDBBranchManager:
    """Manages TiDB Cloud branch lifecycle via REST API."""

    def __init__(self):
        self.public_key = os.getenv('TIDB_CLOUD_PUBLIC_KEY')
        self.private_key = os.getenv('TIDB_CLOUD_PRIVATE_KEY')
        self.project_id = os.getenv('TIDB_CLOUD_PROJECT_ID')
        self.cluster_id = os.getenv('TIDB_CLOUD_CLUSTER_ID')
        self.base_url = "https://api.tidbcloud.com/api/v1beta"

    @property
    def _auth(self):
        if not all([self.public_key, self.private_key]):
            raise ValueError(
                "Missing TiDB Cloud API credentials. "
                "Set TIDB_CLOUD_PUBLIC_KEY, TIDB_CLOUD_PRIVATE_KEY, "
                "TIDB_CLOUD_PROJECT_ID, TIDB_CLOUD_CLUSTER_ID in .env"
            )
        return HTTPDigestAuth(self.public_key, self.private_key)

    @property
    def _branches_url(self):
        return f"{self.base_url}/clusters/{self.cluster_id}/branches"

    # ── Branch Lifecycle ──────────────────────────────────────────

    def create_branch(self, branch_name: str, timeout_seconds: int = 120) -> dict:
        """
        Creates a new branch and waits until it's ACTIVE.

        Returns:
            dict with keys: branch_id, host, port, user, password, status
        """
        print(f"🔀 Creating branch '{branch_name}'...")

        payload = {"displayName": branch_name}
        response = requests.post(
            self._branches_url,
            json=payload,
            auth=self._auth,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code not in (200, 201):
            raise Exception(
                f"Failed to create branch: {response.status_code} — {response.text}"
            )

        branch_data = response.json()
        branch_id = branch_data.get('id') or branch_data.get('branchId')

        # Poll until the branch is ready
        return self._wait_for_active(branch_id, timeout_seconds)

    def _wait_for_active(self, branch_id: str, timeout_seconds: int = 120) -> dict:
        """Polls the branch status until ACTIVE or timeout."""
        url = f"{self._branches_url}/{branch_id}"
        start = time.time()
        poll_interval = 3  # seconds

        while time.time() - start < timeout_seconds:
            res = requests.get(url, auth=self._auth)
            if res.status_code != 200:
                raise Exception(f"Failed to poll branch: {res.status_code} — {res.text}")

            data = res.json()
            state = data.get('state', '').upper()

            if state in ('ACTIVE', 'READY'):
                # Extract connection info
                endpoints = data.get('endpoints', {})
                public_ep = endpoints.get('public', {})
                raw_host = public_ep.get('host', '') or os.getenv('TIDB_HOST', '')
                branch_user = data.get('userPrefix', '') + '.root'

                # On TiDB Starter, branches share the production gateway hostname.
                # Use the REAL host so the connection actually resolves.
                # The safety check in apply_ddl_on_branch compares the USER prefix
                # (not the host) to distinguish branch from production — branches
                # always get a different userPrefix from the TiDB Cloud API.
                return {
                    "branch_id": branch_id,
                    "branch_name": data.get('displayName', ''),
                    "host": raw_host,
                    "port": int(public_ep.get('port', 4000)),
                    "user": branch_user,
                    "password": os.getenv('TIDB_PASSWORD', ''),
                    "status": state,
                }
            elif state in ('FAILED', 'DELETED'):
                raise Exception(f"Branch entered {state} state: {data}")

            print(f"   ⏳ Branch status: {state} (waiting...)")
            time.sleep(poll_interval)

        raise TimeoutError(
            f"Branch {branch_id} did not become ACTIVE within {timeout_seconds}s"
        )

    def list_branches(self) -> list:
        """Lists all branches on the cluster."""
        response = requests.get(self._branches_url, auth=self._auth)
        if response.status_code != 200:
            raise Exception(f"Failed to list branches: {response.text}")

        data = response.json()
        branches = data.get('branches', data.get('items', []))
        return [
            {
                "branch_id": b.get('id') or b.get('branchId'),
                "name": b.get('displayName', ''),
                "state": b.get('state', ''),
                "created_at": b.get('createTime', ''),
            }
            for b in branches
        ]

    def delete_branch(self, branch_id: str) -> bool:
        """Deletes a branch by ID. Returns True on success."""
        url = f"{self._branches_url}/{branch_id}"
        response = requests.delete(url, auth=self._auth)

        if response.status_code in (200, 204):
            print(f"🗑️  Branch {branch_id} deleted.")
            return True
        else:
            print(f"⚠️  Failed to delete branch: {response.status_code} — {response.text}")
            return False

    def cleanup_agent_branches(self):
        """Deletes all branches created by this agent (prefixed with 'fix-')."""
        branches = self.list_branches()
        deleted = 0
        for b in branches:
            if b['name'].startswith('fix-'):
                self.delete_branch(b['branch_id'])
                deleted += 1
        return deleted


# ── Quick test ────────────────────────────────────────────────────
if __name__ == "__main__":
    mgr = TiDBBranchManager()
    print("Current branches:")
    for b in mgr.list_branches():
        print(f"  • {b['name']} ({b['state']})")
