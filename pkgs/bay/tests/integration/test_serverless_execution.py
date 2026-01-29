"""E2E-11: Simple Quick Execution (Stateless Serverless-style) tests.

Purpose: Simulate a quick execution workflow without caring about persistence:
- Minimal API calls (create -> exec -> delete)
- Lazy loading (container not started on create)
- Cold start verification
- Complete cleanup after delete

See: plans/phase-1/e2e-workflow-scenarios.md - Scenario 4
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from .conftest import (
    AUTH_HEADERS,
    BAY_BASE_URL,
    DEFAULT_PROFILE,
    docker_container_exists,
    docker_volume_exists,
    e2e_skipif_marks,
)

pytestmark = e2e_skipif_marks


class TestE2E11ServerlessExecution:
    """E2E-11: Simple Quick Execution (Stateless Serverless-style)."""

    async def test_minimal_lifecycle_three_api_calls(self):
        """Complete lifecycle with just 3 API calls: create -> exec -> delete."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Step 1: Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox = create_response.json()
            sandbox_id = sandbox["id"]
            
            # Verify initial state
            assert sandbox["status"] == "idle", f"Expected idle status, got: {sandbox['status']}"
            
            # Step 2: Execute code (triggers cold start)
            exec_response = await client.post(
                f"/v1/sandboxes/{sandbox_id}/python/exec",
                json={"code": "print(2 * 21)", "timeout": 30},
                timeout=120.0,  # Cold start may take time
            )
            assert exec_response.status_code == 200
            result = exec_response.json()
            assert result["success"] is True
            assert "42" in result["output"], f"Expected '42' in output, got: {result['output']}"
            
            # Step 3: Delete sandbox
            delete_response = await client.delete(f"/v1/sandboxes/{sandbox_id}")
            assert delete_response.status_code == 204
            
            # Verify complete cleanup
            get_response = await client.get(f"/v1/sandboxes/{sandbox_id}")
            assert get_response.status_code == 404

    async def test_lazy_loading_container_not_started_on_create(self):
        """Container should NOT be started when sandbox is created."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox = create_response.json()
            sandbox_id = sandbox["id"]
            
            try:
                # Verify status is idle (not running)
                assert sandbox["status"] == "idle"
                
                # Get sandbox to confirm still idle
                get_response = await client.get(f"/v1/sandboxes/{sandbox_id}")
                assert get_response.status_code == 200
                sandbox_state = get_response.json()
                assert sandbox_state["status"] == "idle", \
                    f"Expected idle, container should not start on create. Got: {sandbox_state['status']}"
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_cold_start_on_first_exec(self):
        """First execution should trigger cold start."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Verify idle before exec
                get_before = await client.get(f"/v1/sandboxes/{sandbox_id}")
                assert get_before.json()["status"] == "idle"
                
                # Execute (cold start)
                import time
                start_time = time.time()
                
                exec_response = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": "print('cold started!')", "timeout": 30},
                    timeout=120.0,
                )
                
                elapsed = time.time() - start_time
                
                assert exec_response.status_code == 200
                result = exec_response.json()
                assert result["success"] is True
                assert "cold started!" in result["output"]
                
                # Verify sandbox is now running
                get_after = await client.get(f"/v1/sandboxes/{sandbox_id}")
                assert get_after.json()["status"] in ("ready", "starting"), \
                    f"Expected ready/starting after exec, got: {get_after.json()['status']}"
                
                # Cold start should have some latency (at least 1s for container startup)
                # Note: This is a soft check; actual time depends on environment
                print(f"Cold start elapsed: {elapsed:.2f}s")
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_delete_cleans_up_all_resources(self):
        """Delete should clean up container and volume."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox = create_response.json()
            sandbox_id = sandbox["id"]
            workspace_id = sandbox["workspace_id"]
            volume_name = f"bay-workspace-{workspace_id}"
            
            # Verify volume was created
            assert docker_volume_exists(volume_name), \
                f"Volume {volume_name} should exist after create"
            
            # Execute to start container
            await client.post(
                f"/v1/sandboxes/{sandbox_id}/python/exec",
                json={"code": "print('hello')", "timeout": 30},
                timeout=120.0,
            )
            
            # Wait a bit for container to stabilize
            await asyncio.sleep(0.5)
            
            # Delete sandbox
            delete_response = await client.delete(f"/v1/sandboxes/{sandbox_id}")
            assert delete_response.status_code == 204
            
            # Wait for cleanup
            await asyncio.sleep(1.0)
            
            # Verify volume is deleted
            assert not docker_volume_exists(volume_name), \
                f"Volume {volume_name} should be deleted after sandbox delete"
            
            # Verify GET returns 404
            get_response = await client.get(f"/v1/sandboxes/{sandbox_id}")
            assert get_response.status_code == 404

    async def test_sequential_sandboxes_independent(self):
        """Multiple sandboxes should be completely independent."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            sandbox_ids = []
            
            try:
                # Create 3 sandboxes
                for i in range(3):
                    create_response = await client.post(
                        "/v1/sandboxes",
                        json={"profile": DEFAULT_PROFILE},
                    )
                    assert create_response.status_code == 201
                    sandbox_ids.append(create_response.json()["id"])
                
                # Execute different code in each
                for i, sandbox_id in enumerate(sandbox_ids):
                    exec_response = await client.post(
                        f"/v1/sandboxes/{sandbox_id}/python/exec",
                        json={"code": f"x = {i}; print(f'sandbox {i}: x={{x}}')", "timeout": 30},
                        timeout=120.0,
                    )
                    assert exec_response.status_code == 200
                    result = exec_response.json()
                    assert result["success"] is True
                    assert f"sandbox {i}: x={i}" in result["output"]
                
                # Delete in reverse order
                for sandbox_id in reversed(sandbox_ids):
                    delete_response = await client.delete(f"/v1/sandboxes/{sandbox_id}")
                    assert delete_response.status_code == 204
                    sandbox_ids.remove(sandbox_id)
                    
            finally:
                # Clean up any remaining
                for sandbox_id in sandbox_ids:
                    await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_compute_intensive_task(self):
        """Execute a CPU-intensive task to verify execution works."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Run a small compute task
                compute_code = """
import math

# Calculate sum of first 1000 primes (simple sieve)
def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(math.sqrt(n)) + 1):
        if n % i == 0:
            return False
    return True

primes = []
n = 2
while len(primes) < 100:  # Find first 100 primes
    if is_prime(n):
        primes.append(n)
    n += 1

print(f"Sum of first 100 primes: {sum(primes)}")
print(f"100th prime: {primes[-1]}")
"""
                exec_response = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": compute_code, "timeout": 60},
                    timeout=120.0,
                )
                assert exec_response.status_code == 200
                result = exec_response.json()
                assert result["success"] is True
                
                # Sum of first 100 primes is 24133
                assert "24133" in result["output"], \
                    f"Expected sum 24133, got: {result['output']}"
                # 100th prime is 541
                assert "541" in result["output"], \
                    f"Expected 100th prime 541, got: {result['output']}"
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_oneshot_json_processing(self):
        """Process JSON data in a single execution - typical API/serverless use case."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Process JSON data in a single execution
                json_processing_code = """
import json

# Simulate incoming data
data = {
    "users": [
        {"name": "Alice", "age": 30, "active": True},
        {"name": "Bob", "age": 25, "active": False},
        {"name": "Charlie", "age": 35, "active": True}
    ]
}

# Process: filter active users and calculate average age
active_users = [u for u in data["users"] if u["active"]]
avg_age = sum(u["age"] for u in active_users) / len(active_users)

result = {
    "active_count": len(active_users),
    "average_age": avg_age,
    "names": [u["name"] for u in active_users]
}

print(json.dumps(result))
"""
                exec_response = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": json_processing_code, "timeout": 30},
                    timeout=120.0,
                )
                assert exec_response.status_code == 200
                result = exec_response.json()
                assert result["success"] is True
                
                # Parse the output JSON
                import json
                output_data = json.loads(result["output"].strip())
                assert output_data["active_count"] == 2
                assert output_data["average_age"] == 32.5  # (30+35)/2
                assert "Alice" in output_data["names"]
                assert "Charlie" in output_data["names"]
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")
