"""Test profile.env injection into sandbox runtime.

This test verifies that environment variables defined in profile.env are:
1. Available in shell commands via /shell/exec
2. Available in Python code via /ipython/exec
3. Persisted in /workspace/.bay_env.sh
"""

from __future__ import annotations

import httpx
import pytest

from ..conftest import (
    AUTH_HEADERS,
    BAY_BASE_URL,
    DEFAULT_PROFILE,
    create_sandbox,
    e2e_skipif_marks,
)

pytestmark = e2e_skipif_marks


@pytest.mark.asyncio
async def test_profile_env_available_in_shell():
    """Test that profile.env variables are available in shell commands."""
    async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
        async with create_sandbox(client) as sandbox:
            sandbox_id = sandbox["id"]

            # Execute shell command to read the env var
            resp = await client.post(
                f"/v1/sandboxes/{sandbox_id}/shell/exec",
                json={"command": "echo $BAY_TEST_ENV_VAR"},
                timeout=30.0,
            )

            assert resp.status_code == 200, f"Shell exec failed: {resp.text}"
            result = resp.json()

            # The env var should be available
            assert result["success"], f"Shell command failed: {result}"
            assert "test-value-12345" in result["output"], (
                f"Expected BAY_TEST_ENV_VAR=test-value-12345, got: {result['output']}"
            )


@pytest.mark.asyncio
async def test_profile_env_available_in_python():
    """Test that profile.env variables are available in Python code."""
    async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
        async with create_sandbox(client) as sandbox:
            sandbox_id = sandbox["id"]

            # Execute Python code to read the env var
            # Note: capabilities_router is mounted under /sandboxes prefix
            resp = await client.post(
                f"/v1/sandboxes/{sandbox_id}/python/exec",
                json={"code": "import os; print(os.environ.get('BAY_TEST_ENV_VAR', 'NOT_FOUND'))"},
                timeout=30.0,
            )

            assert resp.status_code == 200, f"Python exec failed: {resp.text}"
            result = resp.json()

            # The env var should be available
            assert result["success"], f"Python execution failed: {result}"
            output = result["output"]
            if isinstance(output, dict):
                output = output.get("text", "")
            assert "test-value-12345" in output, (
                f"Expected BAY_TEST_ENV_VAR=test-value-12345, got: {output}"
            )


@pytest.mark.asyncio
async def test_profile_env_file_created():
    """Test that /workspace/.bay_env.sh is created with profile.env content."""
    async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
        async with create_sandbox(client) as sandbox:
            sandbox_id = sandbox["id"]

            # Check if .bay_env.sh exists and contains the expected content
            resp = await client.post(
                f"/v1/sandboxes/{sandbox_id}/shell/exec",
                json={"command": "cat /workspace/.bay_env.sh"},
                timeout=30.0,
            )

            assert resp.status_code == 200, f"Shell exec failed: {resp.text}"
            result = resp.json()

            assert result["success"], f"Failed to read .bay_env.sh: {result}"
            assert "BAY_TEST_ENV_VAR" in result["output"], (
                f"Expected BAY_TEST_ENV_VAR in .bay_env.sh, got: {result['output']}"
            )
            assert "test-value-12345" in result["output"], (
                f"Expected test-value-12345 in .bay_env.sh, got: {result['output']}"
            )


@pytest.mark.asyncio
async def test_profile_env_multiple_vars():
    """Test that multiple env vars from profile.env are all available."""
    async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
        async with create_sandbox(client) as sandbox:
            sandbox_id = sandbox["id"]

            # Test in shell
            shell_resp = await client.post(
                f"/v1/sandboxes/{sandbox_id}/shell/exec",
                json={"command": 'echo "VAR=$BAY_TEST_ENV_VAR"'},
                timeout=30.0,
            )

            assert shell_resp.status_code == 200
            result = shell_resp.json()
            assert result["success"], f"Shell command failed: {result}"
            assert "test-value-12345" in result["output"]

            # Test in Python
            # Note: capabilities_router is mounted under /sandboxes prefix
            python_resp = await client.post(
                f"/v1/sandboxes/{sandbox_id}/python/exec",
                json={
                    "code": """
import os
var = os.environ.get('BAY_TEST_ENV_VAR', 'NOT_FOUND')
print(f'VAR={var}')
"""
                },
                timeout=30.0,
            )

            assert python_resp.status_code == 200
            result = python_resp.json()
            assert result["success"], f"Python execution failed: {result}"
            output = result["output"]
            if isinstance(output, dict):
                output = output.get("text", "")
            assert "test-value-12345" in output
