"""E2E-07: Idempotency-Key support tests.

Purpose: Verify idempotent sandbox creation with Idempotency-Key header.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from .conftest import AUTH_HEADERS, BAY_BASE_URL, DEFAULT_PROFILE, e2e_skipif_marks

pytestmark = e2e_skipif_marks


class TestE2E07Idempotency:
    """E2E-07: Idempotency-Key support."""

    async def test_idempotent_create_returns_same_response(self):
        """Same Idempotency-Key returns same sandbox on retry."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            idempotency_key = f"test-idem-{uuid.uuid4()}"
            request_body = {"profile": DEFAULT_PROFILE}
            
            # First request - creates sandbox
            response1 = await client.post(
                "/v1/sandboxes",
                json=request_body,
                headers={"Idempotency-Key": idempotency_key},
            )
            assert response1.status_code == 201
            sandbox1 = response1.json()
            sandbox_id = sandbox1["id"]
            
            try:
                # Second request with same key - should return cached response
                response2 = await client.post(
                    "/v1/sandboxes",
                    json=request_body,
                    headers={"Idempotency-Key": idempotency_key},
                )
                
                # Should return 201 (from cache) with same sandbox
                assert response2.status_code == 201, \
                    f"Expected 201 from cache, got: {response2.status_code}"
                sandbox2 = response2.json()
                
                # Same sandbox ID
                assert sandbox2["id"] == sandbox1["id"], \
                    f"Expected same sandbox ID, got: {sandbox2['id']} vs {sandbox1['id']}"
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_idempotent_create_conflict_on_different_body(self):
        """Same Idempotency-Key with different body returns 409."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            idempotency_key = f"test-conflict-{uuid.uuid4()}"
            
            # First request
            response1 = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
                headers={"Idempotency-Key": idempotency_key},
            )
            assert response1.status_code == 201
            sandbox_id = response1.json()["id"]
            
            try:
                # Second request with same key but different body
                response2 = await client.post(
                    "/v1/sandboxes",
                    json={"profile": DEFAULT_PROFILE, "ttl": 3600},  # Different body
                    headers={"Idempotency-Key": idempotency_key},
                )
                
                # Should return 409 conflict
                assert response2.status_code == 409, \
                    f"Expected 409 conflict, got: {response2.status_code}"
                
                error = response2.json()
                assert "error" in error
                assert error["error"]["code"] == "conflict"
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_create_without_idempotency_key(self):
        """Create without Idempotency-Key works normally."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create two sandboxes without idempotency key
            response1 = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert response1.status_code == 201
            sandbox1 = response1.json()
            
            response2 = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert response2.status_code == 201
            sandbox2 = response2.json()
            
            try:
                # Should create two different sandboxes
                assert sandbox1["id"] != sandbox2["id"], \
                    "Without idempotency key, should create separate sandboxes"
                    
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox1['id']}")
                await client.delete(f"/v1/sandboxes/{sandbox2['id']}")

    async def test_invalid_idempotency_key_format(self):
        """Invalid Idempotency-Key format returns 409."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Key with invalid characters
            response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
                headers={"Idempotency-Key": "invalid key with spaces"},
            )
            
            # Should return 409 for invalid format
            assert response.status_code == 409, \
                f"Expected 409 for invalid key format, got: {response.status_code}"
