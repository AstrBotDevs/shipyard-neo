"""Pool maintenance with real transactions and a controlled runtime driver."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from app.config import ProfileConfig, Settings, WarmPoolConfig
from app.drivers.base import ContainerInfo, ContainerStatus
from app.managers.sandbox import SandboxManager
from app.models.sandbox import Sandbox, WarmState
from app.models.session import Session, SessionStatus
from app.services.warm_pool.queue import WarmupQueue, WarmupTask
from app.services.warm_pool.scheduler import WarmPoolScheduler
from tests.fakes import FakeDriver


@pytest.fixture
async def pool(tmp_path, monkeypatch):
    # Separate connections matter: the scheduler's probe can overlap a claim
    # committed by another request, unlike a shared in-memory test session.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = SimpleNamespace(now=datetime(2026, 1, 1), monotonic=1000.0)
    config = WarmPoolConfig(warmup_queue_workers=1, warmup_queue_max_size=1)
    profile = ProfileConfig(id="python-default", warm_pool_size=1, warm_rotate_ttl=300)
    settings = Settings(profiles=[profile], warm_pool=config)
    driver = FakeDriver()
    queue = WarmupQueue(config)
    scheduler = WarmPoolScheduler(config, queue)
    monkeypatch.setattr("app.db.session.get_async_session", factory)
    monkeypatch.setattr("app.api.dependencies.get_driver", lambda: driver)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.managers.sandbox.sandbox.get_settings", lambda: settings)
    monkeypatch.setattr("app.managers.sandbox.sandbox.utcnow", lambda: clock.now)
    monkeypatch.setattr("app.services.warm_pool.scheduler.utcnow", lambda: clock.now)
    monkeypatch.setattr(
        "app.services.warm_pool.scheduler.time",
        SimpleNamespace(monotonic=lambda: clock.monotonic),
    )
    monkeypatch.setattr("app.services.warm_pool.lifecycle._warm_pool_scheduler", scheduler)

    async def create(*, ready_for=None):
        async with factory() as db:
            manager = SandboxManager(driver, db)
            sandbox = await manager.create_warm_sandbox(profile.id)
            if ready_for is not None:
                session = Session(
                    id=f"session-{sandbox.id}",
                    sandbox_id=sandbox.id,
                    container_id=f"container-{sandbox.id}",
                    endpoint="http://ready",
                    observed_state=SessionStatus.RUNNING,
                )
                db.add(session)
                sandbox.current_session_id = session.id
                await db.commit()
                driver.set_status_override(
                    session.container_id,
                    ContainerInfo(
                        container_id=session.container_id, status=ContainerStatus.RUNNING
                    ),
                )
                await manager.mark_warm_available(sandbox.id, ready_for)
            return sandbox.id

    async def rows():
        async with factory() as db:
            return (
                (await db.execute(select(Sandbox).where(Sandbox.deleted_at.is_(None))))
                .scalars()
                .all()
            )

    async def claim():
        async with factory() as db:
            return await SandboxManager(driver, db).claim_warm_sandbox("user", profile.id, ttl=600)

    def advance(seconds):
        clock.now += timedelta(seconds=seconds)
        clock.monotonic += seconds

    yield SimpleNamespace(
        factory=factory,
        clock=clock,
        config=config,
        profile=profile,
        driver=driver,
        queue=queue,
        scheduler=scheduler,
        create=create,
        rows=rows,
        claim=claim,
        advance=advance,
    )
    await scheduler.stop()
    await queue.stop()
    await engine.dispose()


async def test_claim_wakes_scheduler_and_replenishes_without_scan_delay(pool, monkeypatch):
    original = await pool.create(ready_for=300)
    created = asyncio.Event()
    create = pool.scheduler._create_warm_sandbox

    async def observe(profile):
        result = await create(profile)
        created.set()
        return result

    monkeypatch.setattr(pool.scheduler, "_create_warm_sandbox", observe)
    await pool.scheduler.start()
    claimed = await pool.claim()
    assert claimed.id == original
    assert claimed.expires_at == pool.clock.now + timedelta(seconds=600)
    await asyncio.wait_for(created.wait(), 2)
    rows = await pool.rows()
    assert len(rows) == 2
    assert sum(s.is_warm_pool for s in rows) == 1
    assert pool.queue.depth == 1


async def test_notifications_coalesce_and_notification_during_cycle_is_retained(pool, monkeypatch):
    entered, release, again = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def maintain():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        else:
            again.set()

    monkeypatch.setattr(pool.scheduler, "run_once", maintain)
    for _ in range(20):
        pool.scheduler.request_maintenance()
    await pool.scheduler.start()
    await asyncio.wait_for(entered.wait(), 1)
    for _ in range(20):
        pool.scheduler.request_maintenance()
    release.set()
    await asyncio.wait_for(again.wait(), 1)
    await pool.scheduler.stop()
    assert calls == 2


@pytest.mark.parametrize("policy", ["drop_newest", "drop_oldest"])
async def test_queue_overflow_is_recovered_without_evicting_foreground_work(pool, policy):
    pool.config.warmup_queue_drop_policy = policy
    pool.queue.enqueue(sandbox_id="foreground", owner="user")
    assert await pool.scheduler._maintain_profile(pool.profile) == 1
    pending = (await pool.rows())[0]
    assert pending.warm_state is None
    assert pool.queue.is_pending("foreground")
    assert not pool.queue.is_pending(pending.id)

    # Simulate the worker finishing the foreground task.
    task = pool.queue._queue.get_nowait()
    pool.queue._queue.task_done()
    pool.queue._pending.discard(task.sandbox_id)
    pool.advance(30)
    assert await pool.scheduler._maintain_profile(pool.profile) == 0
    assert pool.queue.is_pending(pending.id)
    assert len(await pool.rows()) == 1
    assert await pool.scheduler._maintain_profile(pool.profile) == 0
    assert pool.queue.stats.enqueue_total == 2


async def test_failed_warmup_retries_after_interval_then_becomes_claimable(pool, monkeypatch):
    attempts = 0

    async def ensure(manager, sandbox):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("runtime temporarily unavailable")
        session = Session(
            id="recovered-session",
            sandbox_id=sandbox.id,
            container_id="recovered-container",
            endpoint="http://ready",
            observed_state=SessionStatus.RUNNING,
        )
        manager._db.add(session)
        sandbox.current_session_id = session.id
        await manager._db.commit()
        return session

    monkeypatch.setattr(SandboxManager, "ensure_running", ensure)
    await pool.queue.start()
    await pool.scheduler._maintain_profile(pool.profile)
    await asyncio.wait_for(pool.queue._queue.join(), 2)
    assert pool.queue.stats.failure_total == 1
    assert await pool.claim() is None
    assert not pool.queue.is_pending((await pool.rows())[0].id)
    for _ in range(3):
        await pool.scheduler._maintain_profile(pool.profile)
    assert attempts == 1
    pool.advance(30)
    await pool.scheduler._maintain_profile(pool.profile)
    await asyncio.wait_for(pool.queue._queue.join(), 2)
    assert attempts == 2
    assert pool.queue.stats.success_total == 1
    assert pool.queue.estimated_warmup_seconds(pool.profile.id) > 0
    assert await pool.claim() is not None


async def test_restart_recovers_persisted_pending_without_creating_more(pool):
    pending = await pool.create()
    assert await pool.scheduler._maintain_profile(pool.profile) == 0
    assert pool.queue.is_pending(pending)
    assert len(await pool.rows()) == 1


async def test_early_replacement_keeps_old_ready_and_caps_surge(pool):
    old = await pool.create(ready_for=20)
    assert await pool.scheduler._maintain_profile(pool.profile) == 1
    rows = {s.id: s for s in await pool.rows()}
    assert rows[old].warm_state == WarmState.AVAILABLE.value
    assert rows[old].warm_rotate_at == pool.clock.now + timedelta(seconds=20)
    assert len(rows) == 2
    for _ in range(3):
        assert await pool.scheduler._maintain_profile(pool.profile) == 0
    assert len(await pool.rows()) == 2
    # The prepared but unready replacement must never win a claim.
    assert (await pool.claim()).id == old
    assert await pool.claim() is None


@pytest.mark.parametrize("replacement_ready", [False, True])
async def test_expiration_is_preserved_even_if_replacement_is_not_ready(pool, replacement_ready):
    old = await pool.create(ready_for=20)
    await pool.scheduler._maintain_profile(pool.profile)
    replacement = next(s for s in await pool.rows() if s.id != old)
    if replacement_ready:
        async with pool.factory() as db:
            await SandboxManager(pool.driver, db).mark_warm_available(replacement.id, 300)
        # A real warmup is still represented as pending in this controlled queue,
        # so reconciliation will leave its just-published result alone.
    pool.advance(21)
    assert await pool.scheduler._maintain_profile(pool.profile) == 0
    rows = await pool.rows()
    assert [s.id for s in rows] == [replacement.id]
    claimed = await pool.claim()
    assert (claimed is not None) == replacement_ready
    assert f"container-{old}" in pool.driver.destroy_calls


async def test_failed_retirement_is_retried_without_unbounded_replacements(pool, monkeypatch):
    await pool.create(ready_for=-1)
    delete = SandboxManager.delete
    monkeypatch.setattr(SandboxManager, "delete", AsyncMock(side_effect=RuntimeError("busy")))
    assert await pool.scheduler._maintain_profile(pool.profile) == 1
    for _ in range(3):
        assert await pool.scheduler._maintain_profile(pool.profile) == 0
    assert len(await pool.rows()) == 2
    monkeypatch.setattr(SandboxManager, "delete", delete)
    # Cleanup is required even though a pending replacement already fills target.
    assert await pool.scheduler._maintain_profile(pool.profile) == 0
    assert len(await pool.rows()) == 1


async def test_creation_failure_is_throttled_during_claim_bursts(pool, monkeypatch):
    create = AsyncMock(side_effect=RuntimeError("driver unavailable"))
    monkeypatch.setattr(pool.scheduler, "_create_warm_sandbox", create)
    for _ in range(3):
        assert await pool.scheduler._maintain_profile(pool.profile) == 0
    assert create.await_count == 1
    pool.advance(30)
    await pool.scheduler._maintain_profile(pool.profile)
    assert create.await_count == 2


async def test_claim_during_health_probe_is_never_reset_or_retired(pool, monkeypatch):
    sandbox_id = await pool.create(ready_for=-1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def probe(*args):
        entered.set()
        await release.wait()
        return False

    monkeypatch.setattr(pool.scheduler, "_is_session_runtime_healthy", probe)
    task = asyncio.create_task(pool.scheduler._maintain_profile(pool.profile))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        claimed = await pool.claim()
        assert claimed.id == sandbox_id
        release.set()
        await asyncio.wait_for(task, 2)
    finally:
        release.set()
        await task
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, sandbox_id)
        session = await db.get(Session, sandbox.current_session_id)
        assert sandbox.owner == "user"
        assert sandbox.warm_state == WarmState.CLAIMED.value
        assert sandbox.deleted_at is None
        assert session.observed_state == SessionStatus.RUNNING
        assert session.endpoint == "http://ready"
    assert not pool.queue.is_pending(sandbox_id)
    assert pool.driver.destroy_calls == []


@pytest.mark.parametrize("state", ["retiring", "claimed", "deleted", "available"])
async def test_late_warmup_completion_cannot_resurrect_or_extend_instance(pool, state):
    sandbox_id = await pool.create(ready_for=300)
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, sandbox_id)
        deadline = sandbox.warm_rotate_at
        if state == "deleted":
            sandbox.deleted_at = pool.clock.now
        else:
            sandbox.warm_state = state
            sandbox.is_warm_pool = state != "claimed"
        await db.commit()
        pool.advance(60)
        await SandboxManager(pool.driver, db).mark_warm_available(sandbox_id, 300)
        await db.refresh(sandbox)
        assert sandbox.warm_rotate_at == deadline
        assert sandbox.warm_state == ("available" if state == "deleted" else state)


@pytest.mark.parametrize("state", ["retiring", "claimed", "deleted"])
async def test_stale_queue_task_does_not_start_a_retired_or_transferred_instance(
    pool, monkeypatch, state
):
    sandbox_id = await pool.create()
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, sandbox_id)
        if state == "deleted":
            sandbox.deleted_at = pool.clock.now
        else:
            sandbox.warm_state = state
            if state == "claimed":
                sandbox.owner = "user"
                sandbox.is_warm_pool = False
        await db.commit()
    ensure = AsyncMock()
    monkeypatch.setattr(SandboxManager, "ensure_running", ensure)
    await pool.queue._process_task(WarmupTask(sandbox_id, "warm-pool"), 0)
    ensure.assert_not_awaited()


async def test_rotation_lead_adapts_to_observed_cost_with_lifetime_bound(pool):
    assert pool.scheduler._rotation_lead_seconds(pool.profile) == 30
    pool.queue._warmup_seconds[pool.profile.id] = 40
    assert pool.scheduler._rotation_lead_seconds(pool.profile) == 90
    pool.queue._warmup_seconds[pool.profile.id] = 10000
    assert pool.scheduler._rotation_lead_seconds(pool.profile) == 150


async def test_worker_to_rotation_to_claim_with_real_managers(pool, monkeypatch):
    """Exercise runtime creation/readiness, worker publication and handover together."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "healthy"})
        )
    ) as client:
        monkeypatch.setattr(
            "app.managers.session.session.http_client_manager", SimpleNamespace(client=client)
        )
        await pool.queue.start()
        assert await pool.scheduler.run_once() == {pool.profile.id: 1}
        await asyncio.wait_for(pool.queue._queue.join(), 3)
        old = (await pool.rows())[0]
        assert old.warm_state == WarmState.AVAILABLE.value

        pool.advance(280)
        assert await pool.scheduler.run_once() == {pool.profile.id: 1}
        await asyncio.wait_for(pool.queue._queue.join(), 3)
        rows = await pool.rows()
        assert len(rows) == 2
        assert all(s.warm_state == WarmState.AVAILABLE.value for s in rows)
        replacement = next(s for s in rows if s.id != old.id)

        pool.advance(21)
        assert await pool.scheduler.run_once() == {pool.profile.id: 0}
        assert [s.id for s in await pool.rows()] == [replacement.id]
        claimed = await pool.claim()
        assert claimed.id == replacement.id
        assert claimed.expires_at == pool.clock.now + timedelta(seconds=600)
        assert pool.queue.stats.success_total == 2
        assert pool.queue.stats.failure_total == 0
        assert pool.driver.destroy_calls


async def test_active_warmup_is_not_enqueued_again_by_recovery(pool, monkeypatch):
    pending = await pool.create()
    entered, release = asyncio.Event(), asyncio.Event()

    async def process(task, worker_id):
        entered.set()
        await release.wait()

    monkeypatch.setattr(pool.queue, "_process_task", process)
    await pool.queue.start()
    await pool.scheduler._maintain_profile(pool.profile)
    await asyncio.wait_for(entered.wait(), 1)
    try:
        for _ in range(3):
            pool.advance(30)
            await pool.scheduler._maintain_profile(pool.profile)
        assert pool.queue.is_pending(pending)
        assert pool.queue.depth == 0
        assert pool.queue.stats.enqueue_total == 1
    finally:
        release.set()
        await asyncio.wait_for(pool.queue._queue.join(), 1)


async def test_stop_and_restart_do_not_keep_sentinels_or_stale_dedup(pool, monkeypatch):
    process = AsyncMock()
    monkeypatch.setattr(pool.queue, "_process_task", process)
    pool.queue.enqueue(sandbox_id="before", owner="warm-pool")
    await pool.queue.start()
    await pool.queue.stop()
    assert not pool.queue.is_pending("before")
    assert pool.queue.depth == 0
    await asyncio.wait_for(pool.queue._queue.join(), 1)

    pool.queue.enqueue(sandbox_id="after", owner="warm-pool")
    await pool.queue.start()
    await asyncio.wait_for(pool.queue._queue.join(), 1)
    assert process.await_args.args[0].sandbox_id == "after"


async def test_drop_oldest_balances_queue_completion(pool, monkeypatch):
    pool.config.warmup_queue_drop_policy = "drop_oldest"
    process = AsyncMock()
    monkeypatch.setattr(pool.queue, "_process_task", process)
    assert pool.queue.enqueue(sandbox_id="old", owner="user")
    assert pool.queue.enqueue(sandbox_id="new", owner="user")
    assert not pool.queue.is_pending("old")
    await pool.queue.start()
    await asyncio.wait_for(pool.queue._queue.join(), 1)
    process.assert_awaited_once()
    assert process.await_args.args[0].sandbox_id == "new"
