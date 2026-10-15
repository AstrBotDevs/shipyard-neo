"""Ownership transactions, retirement cleanup and bounded runtime probing."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.managers.cargo import CargoManager
from app.models.cargo import Cargo
from app.models.sandbox import Sandbox, WarmState
from app.models.session import Session, SessionStatus


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_claim_and_cargo_transfer_roll_back_together(pool, monkeypatch, failure):
    sandbox_id = await pool.create(ready_for=300)
    execute = AsyncSession.execute

    async def fail_cargo_read(db, statement, *args, **kwargs):
        if statement.is_select and any(
            table.name == "cargos" for table in statement.get_final_froms()
        ):
            raise failure("interrupted ownership transfer")
        return await execute(db, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", fail_cargo_read)
        with pytest.raises(failure):
            await pool.claim()

    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, sandbox_id)
        cargo = await db.get(Cargo, sandbox.cargo_id)
        assert sandbox.is_warm_pool
        assert sandbox.warm_state == WarmState.AVAILABLE.value
        assert sandbox.owner == cargo.owner == "warm-pool"
        assert sandbox.expires_at is None
    assert not pool.scheduler._wake.is_set()
    assert (await pool.claim()).id == sandbox_id


async def test_retirement_volume_failure_keeps_durable_retry_and_resource_budget(pool, monkeypatch):
    old = await pool.create(ready_for=0)
    async with pool.factory() as db:
        original = await db.get(Sandbox, old)
        cargo_id = original.cargo_id
        volume = (await db.get(Cargo, cargo_id)).driver_ref
    delete_volume = pool.driver.delete_volume

    async def fail_volume(name):
        if name == volume:
            raise RuntimeError("volume is temporarily busy")
        await delete_volume(name)

    monkeypatch.setattr(pool.driver, "delete_volume", fail_volume)
    for _ in range(3):
        await pool.scheduler.run_once()
        pool.advance(30)
    async with pool.factory() as db:
        old_row = await db.get(Sandbox, old)
        assert old_row.deleted_at is None
        assert old_row.warm_state == WarmState.RETIRING.value
        assert await db.get(Cargo, cargo_id) is not None
    assert len(await pool.rows()) <= 2
    assert len(pool.driver._volumes) <= 2
    monkeypatch.setattr(pool.driver, "delete_volume", delete_volume)
    await pool.scheduler.run_once()
    async with pool.factory() as db:
        assert (await db.get(Sandbox, old)).deleted_at is not None
        assert await db.get(Cargo, cargo_id) is None
    assert volume not in pool.driver._volumes


async def test_runtime_probes_overlap_but_respect_worker_limit(pool, monkeypatch):
    pool.config.warmup_queue_workers = 2
    pool.profile.warm_pool_size = 5
    for _ in range(5):
        await pool.create(ready_for=300)
    entered, release = asyncio.Event(), asyncio.Event()
    active = 0
    peak = 0
    calls = 0

    async def probe(*args):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        if active == 2:
            entered.set()
        try:
            await release.wait()
            return True
        finally:
            active -= 1

    monkeypatch.setattr(pool.scheduler, "_is_session_runtime_healthy", probe)
    task = asyncio.create_task(pool.scheduler.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not task.done()
        assert peak == 2
    finally:
        release.set()
        await task
    assert calls == 5
    assert peak == 2
    assert active == 0


async def test_cancelled_probe_batch_drains_tasks_without_changing_pool(pool, monkeypatch):
    pool.config.warmup_queue_workers = 2
    ids = [await pool.create(ready_for=300) for _ in range(3)]
    entered = asyncio.Event()
    active = 0
    finished = 0

    async def probe(*args):
        nonlocal active, finished
        active += 1
        if active == 2:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1
            finished += 1

    monkeypatch.setattr(pool.scheduler, "_is_session_runtime_healthy", probe)
    task = asyncio.create_task(pool.scheduler.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert active == 0
    assert finished == 2
    async with pool.factory() as db:
        for sandbox_id in ids:
            sandbox = await db.get(Sandbox, sandbox_id)
            session = await db.get(Session, sandbox.current_session_id)
            assert sandbox.warm_state == WarmState.AVAILABLE.value
            assert session.observed_state == SessionStatus.RUNNING
            assert not pool.queue.is_pending(sandbox_id)


async def test_probe_failure_drains_peers_and_does_not_publish_partial_recovery(pool, monkeypatch):
    pool.config.warmup_queue_workers = 2
    ids = [await pool.create(ready_for=300) for _ in range(2)]
    failed, waiting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    peer_finished = False

    async def probe(driver, session):
        nonlocal peer_finished
        if session.sandbox_id == ids[0]:
            failed.set()
            raise RuntimeError("unexpected probe failure")
        waiting.set()
        try:
            await release.wait()
            return False
        finally:
            peer_finished = True

    monkeypatch.setattr(pool.scheduler, "_is_session_runtime_healthy", probe)
    task = asyncio.create_task(pool.scheduler._reconcile_profile_runtime_state(pool.profile.id))
    try:
        await asyncio.wait_for(asyncio.gather(failed.wait(), waiting.wait()), 1)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="unexpected probe failure"):
            await task
    assert peer_finished
    async with pool.factory() as db:
        for sandbox_id in ids:
            sandbox = await db.get(Sandbox, sandbox_id)
            session = await db.get(Session, sandbox.current_session_id)
            assert sandbox.warm_state == WarmState.AVAILABLE.value
            assert session.observed_state == SessionStatus.RUNNING
            assert not pool.queue.is_pending(sandbox_id)


async def test_parallel_stale_probes_cannot_reset_a_concurrent_claim(pool, monkeypatch):
    pool.config.warmup_queue_workers = 2
    ids = [await pool.create(ready_for=300) for _ in range(2)]
    entered, release = asyncio.Event(), asyncio.Event()
    started = 0

    async def probe(*args):
        nonlocal started
        started += 1
        if started == 2:
            entered.set()
        await release.wait()
        return False

    monkeypatch.setattr(pool.scheduler, "_is_session_runtime_healthy", probe)
    task = asyncio.create_task(pool.scheduler._reconcile_profile_runtime_state(pool.profile.id))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        claimed = await pool.claim()
    finally:
        release.set()
        recovered = await task
    assert recovered == 1
    async with pool.factory() as db:
        for sandbox_id in ids:
            sandbox = await db.get(Sandbox, sandbox_id)
            session = await db.get(Session, sandbox.current_session_id)
            if sandbox_id == claimed.id:
                assert sandbox.owner == "user"
                assert sandbox.warm_state == WarmState.CLAIMED.value
                assert session.observed_state == SessionStatus.RUNNING
                assert not pool.queue.is_pending(sandbox_id)
            else:
                assert sandbox.is_warm_pool
                assert sandbox.warm_state is None
                assert session.observed_state == SessionStatus.STOPPED
                assert pool.queue.is_pending(sandbox_id)


async def test_retirement_resumes_after_cargo_is_deleted_before_sandbox_commit(pool, monkeypatch):
    old = await pool.create(ready_for=0)
    async with pool.factory() as db:
        cargo_id = (await db.get(Sandbox, old)).cargo_id
        volume = (await db.get(Cargo, cargo_id)).driver_ref
    delete = CargoManager.delete

    async def interrupt_after_delete(manager, *args, **kwargs):
        await delete(manager, *args, **kwargs)
        raise asyncio.CancelledError("shutdown after cargo deletion")

    with monkeypatch.context() as patch:
        patch.setattr(CargoManager, "delete", interrupt_after_delete)
        with pytest.raises(asyncio.CancelledError):
            await pool.scheduler.run_once()
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, old)
        assert sandbox.deleted_at is None
        assert sandbox.warm_state == WarmState.RETIRING.value
        assert await db.get(Cargo, cargo_id) is None
    assert volume not in pool.driver._volumes
    await pool.scheduler.run_once()
    async with pool.factory() as db:
        assert (await db.get(Sandbox, old)).deleted_at is not None
    assert len(await pool.rows()) == 1
    assert len(pool.driver._volumes) == 1
