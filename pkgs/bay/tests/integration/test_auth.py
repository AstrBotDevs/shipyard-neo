"""E2E-00: Authentication tests.

Purpose: Verify API Key auth is enforced when Bay is started with
`security.allow_anonymous=false`.

Notes:
- The correct API key is provided via AUTH_HEADERS.
- Tests/scripts config uses api_key="e2e-test-api-key".
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import AUTH_HEADERS, BAY_BASE_URL, e2e_skipif_marks

pytestmark = e2e_skipif_marks


class TestE2E00Auth:
    """E2E-00: Authentication tests."""

    async def test_missing_authorization_returns_401(self):
        async with httpx.AsyncClient(base_url=BAY_BASE_URL) as client:
            resp = await client.get("/v1/sandboxes")
            assert resp.status_code == 401, resp.text

    async def test_wrong_api_key_returns_401(self):
        async with httpx.AsyncClient(
            base_url=BAY_BASE_URL,
            headers={"Authorization": "Bearer wrong-key"},
        ) as client:
            resp = await client.get("/v1/sandboxes")
            assert resp.status_code == 401, resp.text

    async def test_valid_api_key_allows_access(self):
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            resp = await client.get("/v1/sandboxes")
            assert resp.status_code == 200, resp.text
