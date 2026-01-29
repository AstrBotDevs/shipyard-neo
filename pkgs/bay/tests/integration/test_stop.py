"""E2E-02: Stop (reclaim compute only) tests.

Purpose: Verify stop destroys session/container but preserves sandbox/workspace.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import AUTH_HEADERS, BAY_BASE_URL, DEFAULT_PROFILE, e2e_skipif_marks

pytestmark = e2e_skipif_marks


class TestE2E02Stop:
    """E2E-02: Stop (reclaim compute only)."""

    async def test_stop_preserves_workspace(self):
        """Stop should destroy session but keep workspace."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create and run sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox = create_response.json()
            sandbox_id = sandbox["id"]
            workspace_id = sandbox["workspace_id"]
            
            try:
                # Trigger session creation by executing code
                exec_response = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": "print('hello')", "timeout": 30},
                    timeout=120.0,
                )
                assert exec_response.status_code == 200
                
                # Get sandbox to verify it has a session
                get_response = await client.get(f"/v1/sandboxes/{sandbox_id}")
                assert get_response.status_code == 200
                assert get_response.json()["status"] in ("ready", "starting")
                
                # Stop sandbox
                stop_response = await client.post(f"/v1/sandboxes/{sandbox_id}/stop")
                assert stop_response.status_code == 200
                
                # Verify sandbox still exists and is idle
                get_response = await client.get(f"/v1/sandboxes/{sandbox_id}")
                assert get_response.status_code == 200
                stopped_sandbox = get_response.json()
                assert stopped_sandbox["status"] == "idle"
                
                # Verify workspace still exists (volume should exist)
                # Note: we can verify by checking if workspace_id is still the same
                assert stopped_sandbox["workspace_id"] == workspace_id
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_stop_is_idempotent(self):
        """Stop should be idempotent - repeated calls don't fail."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox (no session yet)
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Stop multiple times - should not fail
                for _ in range(3):
                    stop_response = await client.post(f"/v1/sandboxes/{sandbox_id}/stop")
                    assert stop_response.status_code == 200
                    
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")
