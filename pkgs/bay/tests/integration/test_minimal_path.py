"""E2E-01: Minimal path (create → python/exec) tests.

Purpose: Verify ensure_running + host_port mapping + ship /ipython/exec.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import AUTH_HEADERS, BAY_BASE_URL, DEFAULT_PROFILE, e2e_skipif_marks

pytestmark = e2e_skipif_marks


class TestE2E01MinimalPath:
    """E2E-01: Minimal path (create → python/exec)."""

    async def test_create_and_exec_python(self):
        """Create sandbox and execute Python code."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Step 1: Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            
            assert create_response.status_code == 201, f"Create failed: {create_response.text}"
            sandbox = create_response.json()
            sandbox_id = sandbox["id"]
            
            try:
                # Verify create response
                assert sandbox["status"] == "idle", f"Expected idle status, got: {sandbox['status']}"
                assert sandbox["workspace_id"] is not None
                assert sandbox["profile"] == DEFAULT_PROFILE
                
                # Step 2: Execute Python code (this triggers ensure_running)
                exec_response = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": "print(1+2)", "timeout": 30},
                    timeout=120.0,  # Allow time for container startup
                )
                
                assert exec_response.status_code == 200, f"Exec failed: {exec_response.text}"
                result = exec_response.json()
                
                # Verify execution result
                assert result["success"] is True, f"Execution failed: {result}"
                assert "3" in result["output"], f"Expected '3' in output, got: {result['output']}"
                
                # Step 3: Verify sandbox now has a session
                get_response = await client.get(f"/v1/sandboxes/{sandbox_id}")
                assert get_response.status_code == 200
                updated_sandbox = get_response.json()
                
                # Status should be ready after execution
                assert updated_sandbox["status"] in ("ready", "starting"), \
                    f"Expected ready/starting status, got: {updated_sandbox['status']}"
                
            finally:
                # Cleanup: Delete sandbox
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_create_response_format(self):
        """Verify create response has correct format."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            
            assert create_response.status_code == 201
            sandbox = create_response.json()
            
            try:
                # Verify required fields
                assert "id" in sandbox
                assert sandbox["id"].startswith("sandbox-")
                assert "status" in sandbox
                assert "profile" in sandbox
                assert "workspace_id" in sandbox
                assert sandbox["workspace_id"].startswith("ws-")
                assert "capabilities" in sandbox
                assert "created_at" in sandbox
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox['id']}")
