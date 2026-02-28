"""Unit tests for sandbox create endpoint with warm pool claim behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse

from app.api.v1.sandboxes import CreateSandboxRequest, create_sandbox
from app.models.sandbox import Sandbox


@pytest.mark.asyncio
async def test_create_sandbox_claim_hit():
    """When claim succeeds, should return claimed sandbox without warmup task."""
    request = CreateSandboxRequest(profile="python-default", ttl=300)
    background_tasks = BackgroundTasks()

    claimed_sandbox = Sandbox(
        id="sandbox-warm-123",
        owner="user-1",
        profile_id="python-default",
        cargo_id="cargo-warm-1",
        is_warm_pool=False,
        warm_state="claimed",
    )

    sandbox_mgr = AsyncMock()
    sandbox_mgr.claim_warm_sandbox.return_value = claimed_sandbox
    sandbox_mgr.create.return_value = None  # Should NOT be called

    idempotency_svc = AsyncMock()
    idempotency_svc.check.return_value = None

    resp = await create_sandbox(
        request=request,
        background_tasks=background_tasks,
        sandbox_mgr=sandbox_mgr,
        idempotency_svc=idempotency_svc,
        owner="user-1",
        idempotency_key=None,
    )

    assert resp.id == "sandbox-warm-123"
    sandbox_mgr.claim_warm_sandbox.assert_awaited_once_with(
        owner="user-1",
        profile_id="python-default",
        ttl=300,
    )
    # create should NOT have been called
    sandbox_mgr.create.assert_not_awaited()
    # No warmup task should be enqueued (already warm)
    assert len(background_tasks.tasks) == 0


@pytest.mark.asyncio
async def test_create_sandbox_claim_miss_falls_back_to_create():
    """When claim fails, should fall back to normal create + warmup."""
    request = CreateSandboxRequest(profile="python-default", ttl=600)
    background_tasks = BackgroundTasks()

    sandbox_mgr = AsyncMock()
    sandbox_mgr.claim_warm_sandbox.return_value = None  # No warm available
    sandbox_mgr.create.return_value = Sandbox(
        id="sandbox-new-456",
        owner="user-1",
        profile_id="python-default",
        cargo_id="cargo-2",
    )

    idempotency_svc = AsyncMock()
    idempotency_svc.check.return_value = None

    # Mock the warmup queue as not available so it falls back to background task
    with patch(
        "app.services.warm_pool.lifecycle.get_warmup_queue",
        return_value=None,
    ):
        resp = await create_sandbox(
            request=request,
            background_tasks=background_tasks,
            sandbox_mgr=sandbox_mgr,
            idempotency_svc=idempotency_svc,
            owner="user-1",
            idempotency_key=None,
        )

    assert resp.id == "sandbox-new-456"
    sandbox_mgr.claim_warm_sandbox.assert_awaited_once()
    sandbox_mgr.create.assert_awaited_once()
    # Fallback warmup task should be enqueued
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_create_sandbox_claim_miss_uses_warmup_queue():
    """When claim fails and queue is available, should use queue instead of background task."""
    request = CreateSandboxRequest(profile="python-default", ttl=300)
    background_tasks = BackgroundTasks()

    sandbox_mgr = AsyncMock()
    sandbox_mgr.claim_warm_sandbox.return_value = None
    sandbox_mgr.create.return_value = Sandbox(
        id="sandbox-queued-789",
        owner="user-1",
        profile_id="python-default",
        cargo_id="cargo-3",
    )

    idempotency_svc = AsyncMock()
    idempotency_svc.check.return_value = None

    # Mock warmup queue
    mock_queue = AsyncMock()
    mock_queue.is_running = True
    mock_queue.enqueue = lambda **kwargs: True

    with patch(
        "app.services.warm_pool.lifecycle.get_warmup_queue",
        return_value=mock_queue,
    ):
        resp = await create_sandbox(
            request=request,
            background_tasks=background_tasks,
            sandbox_mgr=sandbox_mgr,
            idempotency_svc=idempotency_svc,
            owner="user-1",
            idempotency_key=None,
        )

    assert resp.id == "sandbox-queued-789"
    # Should NOT have background task (used queue instead)
    assert len(background_tasks.tasks) == 0


@pytest.mark.asyncio
async def test_create_sandbox_idempotency_hit_skips_claim():
    """Idempotency cache hit should skip claim and warmup."""
    request = CreateSandboxRequest(profile="python-default", ttl=300)
    background_tasks = BackgroundTasks()

    sandbox_mgr = AsyncMock()
    idempotency_svc = AsyncMock()
    idempotency_svc.check.return_value = SimpleNamespace(
        response={"id": "sandbox-cached", "status": "idle"},
        status_code=201,
    )

    resp = await create_sandbox(
        request=request,
        background_tasks=background_tasks,
        sandbox_mgr=sandbox_mgr,
        idempotency_svc=idempotency_svc,
        owner="user-1",
        idempotency_key="idem-key-1",
    )

    assert isinstance(resp, JSONResponse)
    # No claim or create should have been called
    sandbox_mgr.claim_warm_sandbox.assert_not_awaited()
    sandbox_mgr.create.assert_not_awaited()
    assert len(background_tasks.tasks) == 0


@pytest.mark.asyncio
async def test_create_sandbox_claim_hit_saves_idempotency():
    """Claim hit with idempotency key should save the response."""
    request = CreateSandboxRequest(profile="python-default", ttl=300)
    background_tasks = BackgroundTasks()

    sandbox_mgr = AsyncMock()
    sandbox_mgr.claim_warm_sandbox.return_value = Sandbox(
        id="sandbox-warm-idem",
        owner="user-1",
        profile_id="python-default",
        cargo_id="cargo-idem",
    )

    idempotency_svc = AsyncMock()
    idempotency_svc.check.return_value = None

    resp = await create_sandbox(
        request=request,
        background_tasks=background_tasks,
        sandbox_mgr=sandbox_mgr,
        idempotency_svc=idempotency_svc,
        owner="user-1",
        idempotency_key="idem-key-warm",
    )

    assert resp.id == "sandbox-warm-idem"
    idempotency_svc.save.assert_awaited_once()
