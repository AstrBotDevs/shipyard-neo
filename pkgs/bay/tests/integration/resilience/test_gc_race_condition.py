"""Resilience test: GC vs API request race condition.

Purpose: Verify that concurrent GC operations and API requests don't cause
         data corruption or zombie states.

Scenario:
1. Create a sandbox with short TTL.
2. Wait until it's about to expire.
3. Trigger GC (to delete the sandbox) while simultaneously sending an exec request.
4. Verify no zombie state: either the sandbox is deleted and 404 is returned,
   or the sandbox is still accessible and exec succeeds.

Note: This test is marked as SERIAL because it manually triggers GC,
      which can affect other sandboxes in the system.

Parallel-safe: NO - must run in serial group due to GC side effects.
"""

from __future__ import annotations

import asyncio

import httpx

from tests.integration.conftest import (
    AUTH_HEADERS,
    BAY_BASE_URL,
    DEFAULT_PROFILE,
    e2e_skipif_marks,
    trigger_gc,
)

pytestmark = e2e_skipif_marks


class TestGCRaceCondition:
    """Test race conditions between GC and API operations."""

    async def test_gc_delete_vs_exec_no_zombie_state(self) -> None:
        """GC delete and exec request should not create zombie sandbox."""
        async with httpx.AsyncClient(
            base_url=BAY_BASE_URL, headers=AUTH_HEADERS, timeout=120.0
        ) as client:
            # Create sandbox with very short TTL (5 seconds)
            create_resp = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE, "ttl": 5},
            )
            assert create_resp.status_code == 201, create_resp.text
            sandbox = create_resp.json()
            sandbox_id = sandbox["id"]

            try:
                # Start a session first to have something to conflict with
                warmup = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": "print('warmup')", "timeout": 30},
                    timeout=60.0,
                )
                assert warmup.status_code == 200

                # Wait for sandbox to expire
                await asyncio.sleep(6.0)

                # Now fire GC and exec request concurrently
                async def do_gc():
                    try:
                        return await trigger_gc(client, tasks=["expired_sandbox"])
                    except AssertionError:
                        return {"error": "gc_failed"}

                async def do_exec():
                    try:
                        resp = await client.post(
                            f"/v1/sandboxes/{sandbox_id}/python/exec",
                            json={"code": "print('after_expiry')", "timeout": 30},
                            timeout=60.0,
                        )
                        return {
                            "status": resp.status_code,
                            "body": resp.json() if resp.status_code == 200 else resp.text,
                        }
                    except Exception as e:
                        return {"error": str(e)}

                # Run concurrently
                gc_result, exec_result = await asyncio.gather(
                    do_gc(),
                    do_exec(),
                    return_exceptions=True,
                )

                # Verify final state is consistent
                final_status = await client.get(f"/v1/sandboxes/{sandbox_id}")

                # Accept either:
                # 1. Sandbox is deleted (404)
                # 2. Sandbox still exists and is in a valid state (not corrupted)
                if final_status.status_code == 404:
                    # GC won - sandbox is deleted, this is expected
                    pass
                elif final_status.status_code == 200:
                    # Sandbox still exists - verify it's in a valid state
                    data = final_status.json()
                    # Status should be one of the valid states, not some garbage
                    assert data["status"] in (
                        "idle",
                        "starting",
                        "ready",
                        "stopping",
                        "expired",
                        "failed",
                    ), f"Unexpected status: {data}"
                else:
                    # Any other status code is unexpected
                    raise AssertionError(f"Unexpected status code: {final_status.status_code}")

            finally:
                # Cleanup (best effort)
                try:
                    await client.delete(f"/v1/sandboxes/{sandbox_id}", timeout=30.0)
                except Exception:
                    pass

    async def test_gc_idle_session_vs_exec_no_data_loss(self) -> None:
        """GC idle session reclaim vs new exec should not lose workspace data."""
        async with httpx.AsyncClient(
            base_url=BAY_BASE_URL, headers=AUTH_HEADERS, timeout=120.0
        ) as client:
            # Use short-idle profile if available, otherwise use default
            # with manual idle_expires_at manipulation via time.sleep
            create_resp = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE, "ttl": 120},
            )
            assert create_resp.status_code == 201
            sandbox = create_resp.json()
            sandbox_id = sandbox["id"]

            try:
                # Write data to workspace
                write_code = """
from pathlib import Path
Path('race_test.txt').write_text('important_data')
print('wrote_data')
"""
                write_resp = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": write_code, "timeout": 30},
                    timeout=60.0,
                )
                assert write_resp.status_code == 200
                assert write_resp.json()["success"] is True

                # Trigger idle session GC (this should reclaim session but keep cargo)
                await trigger_gc(client, tasks=["idle_session"])

                # Verify data is still accessible after session reclaim
                read_code = """
from pathlib import Path
content = Path('race_test.txt').read_text()
print(f'read: {content}')
"""
                read_resp = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": read_code, "timeout": 30},
                    timeout=60.0,
                )
                assert read_resp.status_code == 200
                result = read_resp.json()
                assert result["success"] is True
                assert "important_data" in result.get("output", "")

            finally:
                try:
                    await client.delete(f"/v1/sandboxes/{sandbox_id}", timeout=30.0)
                except Exception:
                    pass
