"""Authentication tests.

Purpose: Verify API Key auth enforcement when Bay runs with security.allow_anonymous=false.

Parallel-safe: Yes - stateless tests, no sandbox creation.
"""

from __future__ import annotations

import httpx
import pytest

from ..conftest import AUTH_HEADERS, BAY_BASE_URL, e2e_skipif_marks

pytestmark = e2e_skipif_marks


@pytest.mark.parametrize(
    "headers,expected_status",
    [
        pytest.param({}, 401, id="missing_auth"),
        pytest.param({"Authorization": "Bearer wrong-key"}, 401, id="wrong_key"),
        pytest.param(AUTH_HEADERS, 200, id="valid_key"),
    ],
)
async def test_auth(headers: dict, expected_status: int):
    """Verify authentication behavior for different header scenarios."""
    async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=headers) as client:
        resp = await client.get("/v1/sandboxes")
        assert resp.status_code == expected_status, resp.text
