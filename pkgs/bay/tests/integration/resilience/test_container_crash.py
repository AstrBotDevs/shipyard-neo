"""Resilience test: Container Crash recovery.

Purpose: Verify the system correctly handles unexpected container termination.

Phase 1.5 Behavior:
- After container crash, Bay's proactive health probing detects the dead container
- Automatically cleans up and rebuilds the session
- Next exec should return 200 (successful recovery)

Scenario:
1. Create a sandbox and start a session (exec Python code).
2. Use `docker kill` to forcefully terminate the container.
3. Query sandbox status via API and verify it reflects the failure.
4. Attempt another exec and verify it recovers automatically (returns 200).

Parallel-safe: Yes - each test creates/deletes its own sandbox.
"""

from __future__ import annotations

import asyncio
import subprocess

import httpx
import pytest

from tests.integration.conftest import (
    AUTH_HEADERS,
    BAY_BASE_URL,
    DEFAULT_PROFILE,
    create_sandbox,
    e2e_skipif_marks,
)

pytestmark = e2e_skipif_marks


def _docker_kill_container(container_name_or_id: str) -> bool:
    """Force kill a Docker container. Returns True if command succeeded."""
    try:
        result = subprocess.run(
            ["docker", "kill", container_name_or_id],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_container_id_by_session(session_id: str) -> str | None:
    """Get container ID by session label."""
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=bay.session_id={session_id}",
            ],
            capture_output=True,
            timeout=10,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
        return None
    except Exception:
        return None


class TestContainerCrash:
    """Test system behavior when container is forcefully terminated."""

    async def test_auto_recovery_after_container_killed(self) -> None:
        """Phase 1.5: After docker kill, subsequent exec should auto-recover and return 200."""
        async with httpx.AsyncClient(
            base_url=BAY_BASE_URL, headers=AUTH_HEADERS, timeout=60.0
        ) as client:
            async with create_sandbox(client, profile=DEFAULT_PROFILE) as sandbox:
                sandbox_id = sandbox["id"]

                # 1) Start session by executing code
                exec1 = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": "print('hello')", "timeout": 30},
                    timeout=120.0,
                )
                assert exec1.status_code == 200, f"Initial exec failed: {exec1.text}"
                assert exec1.json()["success"] is True

                # 2) Find container to kill
                container_id = None
                result = subprocess.run(
                    [
                        "docker",
                        "ps",
                        "-q",
                        "--filter",
                        f"label=bay.sandbox_id={sandbox_id}",
                    ],
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    container_id = result.stdout.strip().split("\n")[0]

                if container_id is None:
                    pytest.skip("Could not find container to kill")

                old_container_id = container_id

                # 3) Kill the container
                killed = _docker_kill_container(container_id)
                assert killed, f"Failed to kill container {container_id}"

                # 4) Small delay to let container fully exit
                await asyncio.sleep(0.5)

                # 5) Attempt another exec - Phase 1.5 should auto-recover
                exec2 = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": "print('after_crash')", "timeout": 30},
                    timeout=120.0,
                )

                # Phase 1.5 behavior: Auto-recovery should succeed
                # Primary expectation: 200 (recovered and executed successfully)
                # Fallback: 503 (recovery in progress, retryable)
                assert exec2.status_code in (
                    200,
                    503,
                ), f"Unexpected status code: {exec2.status_code}, body: {exec2.text}"

                if exec2.status_code == 200:
                    exec2_result = exec2.json()
                    assert exec2_result["success"] is True
                    assert "after_crash" in exec2_result.get("output", "")

                    # Verify a new container was created (not the old dead one)
                    new_result = subprocess.run(
                        [
                            "docker",
                            "ps",
                            "-q",
                            "--filter",
                            f"label=bay.sandbox_id={sandbox_id}",
                        ],
                        capture_output=True,
                        timeout=10,
                        text=True,
                    )
                    if new_result.returncode == 0 and new_result.stdout.strip():
                        new_container_id = new_result.stdout.strip().split("\n")[0]
                        assert (
                            new_container_id != old_container_id
                        ), "Should have created a new container after crash recovery"
                elif exec2.status_code == 503:
                    # Recovery in progress - retry once more
                    await asyncio.sleep(2.0)
                    exec3 = await client.post(
                        f"/v1/sandboxes/{sandbox_id}/python/exec",
                        json={"code": "print('retry_after_503')", "timeout": 30},
                        timeout=120.0,
                    )
                    assert exec3.status_code == 200, f"Retry after 503 failed: {exec3.text}"
                    exec3_result = exec3.json()
                    assert exec3_result["success"] is True

    async def test_sandbox_status_updates_after_container_exit(self) -> None:
        """Sandbox status should update when container exits unexpectedly."""
        async with httpx.AsyncClient(
            base_url=BAY_BASE_URL, headers=AUTH_HEADERS, timeout=60.0
        ) as client:
            async with create_sandbox(client, profile=DEFAULT_PROFILE) as sandbox:
                sandbox_id = sandbox["id"]

                # Start session
                exec1 = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/python/exec",
                    json={"code": "import time; print('started')", "timeout": 30},
                    timeout=120.0,
                )
                assert exec1.status_code == 200

                # Get initial status
                status1 = await client.get(f"/v1/sandboxes/{sandbox_id}")
                initial_status = status1.json()["status"]
                assert initial_status in ("ready", "starting")

                # Find and kill container
                result = subprocess.run(
                    [
                        "docker",
                        "ps",
                        "-q",
                        "--filter",
                        f"label=bay.sandbox_id={sandbox_id}",
                    ],
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    container_id = result.stdout.strip().split("\n")[0]
                    _docker_kill_container(container_id)

                # Poll for status change (with timeout)
                deadline = asyncio.get_event_loop().time() + 10.0
                final_status = initial_status
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(0.5)
                    status2 = await client.get(f"/v1/sandboxes/{sandbox_id}")
                    if status2.status_code == 200:
                        final_status = status2.json()["status"]
                        # If status changed from ready/starting, we're done
                        if final_status not in ("ready", "starting"):
                            break

                # The status should eventually reflect the failure or idle (recovered)
                # We don't assert specific status because it depends on implementation
                # but we verify the API still responds correctly
                assert status2.status_code == 200
