"""Pre-installed tools verification tests.

Purpose: Verify expected tools are available in the container.

Parallel-safe: Yes - each test creates/deletes its own sandbox.
"""

from __future__ import annotations

import httpx
import pytest

from ..conftest import AUTH_HEADERS, BAY_BASE_URL, create_sandbox, e2e_skipif_marks

pytestmark = e2e_skipif_marks


@pytest.fixture
async def sandbox_id():
    """Create sandbox for tool tests."""
    async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
        async with create_sandbox(client) as sandbox:
            yield sandbox["id"]


@pytest.mark.parametrize(
    "command,expected",
    [
        ("python3 --version", "Python 3"),
        ("node --version", "v"),
        ("npm --version", ""),
        ("pnpm --version", ""),
        ("git --version", "git version"),
        ("curl --version", "curl"),
    ],
    ids=["python", "node", "npm", "pnpm", "git", "curl"],
)
async def test_tool_available(sandbox_id: str, command: str, expected: str):
    """Verify tool is available."""
    async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
        r = await client.post(
            f"/v1/sandboxes/{sandbox_id}/shell/exec",
            json={"command": command},
            timeout=120.0,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["success"] is True, f"Command failed: {result}"
        if expected:
            assert expected in result["output"], f"Expected '{expected}' in output"
