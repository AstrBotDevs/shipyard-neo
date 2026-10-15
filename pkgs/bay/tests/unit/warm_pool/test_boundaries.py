"""Adversarial interleavings, exact deadlines and sustained pool pressure."""

import asyncio
import random
from contextlib import suppress
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ContainerSpec, ProfileConfig
from app.drivers.base import ContainerInfo, ContainerStatus
from app.managers.sandbox import SandboxManager
from app.managers.session import SessionManager
from app.models.cargo import Cargo
from app.models.sandbox import Sandbox, WarmState
from app.models.session import Session, SessionStatus
from app.services.warm_pool.queue import WarmupTask
from app.services.warm_pool.scheduler import WarmPoolScheduler


@pytest.mark.parametrize("remaining", [-0.000001, 0])
async def test_dead_runtime_at_expiry_is_retired_instead_of_getting_a_new_lifetime(pool, remaining):
    old = await pool.create(ready_for=remaining)
    pool.driver.set_status_override(
        f"container-{old}",
        ContainerInfo(container_id=f"container-{old}", status=ContainerStatus.NOT_FOUND),
    )
    await pool.scheduler.run_once()
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, old)
        assert sandbox.deleted_at is not None
        assert sandbox.warm_state == WarmState.RETIRING.value
    assert not pool.queue.is_pending(old)
    assert len(await pool.rows()) == 1


async def test_expiry_while_dead_runtime_probe_is_in_flight_is_not_erased(pool, monkeypatch):
    old = await pool.create(ready_for=0.000001)

    async def probe(*args):
        pool.advance(0.000001)
        return False

    monkeypatch.setattr(pool.scheduler, "_is_session_runtime_healthy", probe)
    await pool.scheduler.run_once()
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, old)
        assert sandbox.deleted_at is not None
        assert sandbox.warm_rotate_at == pool.clock.now
    assert not pool.queue.is_pending(old)


@pytest.mark.parametrize("separate_profiles", [False, True])
async def test_permanently_failing_oldest_job_does_not_starve_later_jobs(
    pool, monkeypatch, separate_profiles
):
    pool.profile.warm_pool_size = 1 if separate_profiles else 2
    other = pool.profile
    if separate_profiles:
        other = ProfileConfig(id="python-other", warm_pool_size=1, warm_rotate_ttl=300)
        pool.settings.profiles.append(other)
    broken = await pool.create()
    pool.advance(1)
    healthy = await pool.create(profile_id=other.id)
    attempted = []

    async def ensure(manager, sandbox):
        attempted.append(sandbox.id)
        if sandbox.id == broken:
            raise RuntimeError("permanent failure of this runtime")
        session = Session(
            id=f"session-{sandbox.id}",
            sandbox_id=sandbox.id,
            container_id=f"container-{sandbox.id}",
            observed_state=SessionStatus.RUNNING,
            endpoint="http://ready",
        )
        manager._db.add(session)
        sandbox.current_session_id = session.id
        await manager._db.commit()
        pool.driver.set_status_override(
            session.container_id,
            ContainerInfo(container_id=session.container_id, status=ContainerStatus.RUNNING),
        )
        return session

    monkeypatch.setattr(SandboxManager, "ensure_running", ensure)
    for _ in range(4):
        # Fill a one-slot queue before its worker gets CPU, as under load or on restart.
        await pool.scheduler.run_once()
        await pool.queue.start()
        await asyncio.wait_for(pool.queue._queue.join(), 1)
        await pool.queue.stop()
        pool.advance(30)

    assert healthy in attempted, attempted
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, healthy)
        assert sandbox.warm_state == WarmState.AVAILABLE.value
    assert len(await pool.rows()) == 2


@pytest.mark.parametrize("remaining", [-0.000001, 0, 0.000001])
async def test_healthy_instance_rotation_at_exact_microsecond_boundary(pool, remaining):
    old = await pool.create(ready_for=remaining)
    await pool.scheduler.run_once()
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, old)
        assert (sandbox.deleted_at is not None) == (remaining <= 0)


@pytest.mark.parametrize("remaining", [29.999999, 30, 30.000001])
async def test_preparation_window_is_inclusive_and_does_not_duplicate_replacements(pool, remaining):
    await pool.create(ready_for=remaining)
    expected = int(remaining <= 30)
    assert await pool.scheduler.run_once() == {pool.profile.id: expected}
    for _ in range(3):
        assert await pool.scheduler.run_once() == {pool.profile.id: 0}
    assert len(await pool.rows()) == 1 + expected


@pytest.mark.parametrize("elapsed", [29.999999, 30, 30.000001])
async def test_retry_gate_uses_monotonic_deadline_under_event_bursts(pool, monkeypatch, elapsed):
    await pool.create()
    calls = []

    def enqueue(**kwargs):
        calls.append(kwargs)
        return False

    monkeypatch.setattr(pool.queue, "enqueue", enqueue)
    await pool.scheduler.run_once()
    pool.clock.monotonic = 1000 + elapsed
    for _ in range(5):
        await pool.scheduler.run_once()
    assert len(calls) == 1 + int(elapsed >= 30)


async def test_retirement_wins_after_claim_has_selected_its_candidate(pool, monkeypatch):
    old = await pool.create(ready_for=0)
    selected, release = asyncio.Event(), asyncio.Event()
    execute = AsyncSession.execute
    paused = False

    async def pause_after_select(db, statement, *args, **kwargs):
        nonlocal paused
        result = await execute(db, statement, *args, **kwargs)
        if asyncio.current_task() is claimant and statement.is_select and not paused:
            paused = True
            selected.set()
            await release.wait()
        return result

    monkeypatch.setattr(AsyncSession, "execute", pause_after_select)
    claimant = asyncio.create_task(pool.claim())
    try:
        await asyncio.wait_for(selected.wait(), 1)
        await asyncio.wait_for(pool.scheduler.run_once(), 2)
        release.set()
        assert await asyncio.wait_for(claimant, 2) is None
    finally:
        release.set()
        await claimant
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, old)
        assert sandbox.deleted_at is not None
        assert sandbox.owner == "warm-pool"
        assert sandbox.warm_state == WarmState.RETIRING.value


async def test_twenty_four_claims_racing_expiration_never_share_or_delete_a_claimed_instance(pool):
    await pool.create(ready_for=0)
    await pool.create(ready_for=300)
    outcomes = await asyncio.wait_for(
        asyncio.gather(*(pool.claim() for _ in range(24)), pool.scheduler.run_once()), 5
    )
    claimed = [s for s in outcomes[:-1] if s is not None]
    assert claimed
    assert len({s.id for s in claimed}) == len(claimed)
    await pool.scheduler.run_once()
    async with pool.factory() as db:
        for result in claimed:
            stored = await db.get(Sandbox, result.id)
            assert stored.deleted_at is None
            assert stored.owner == "user"
            assert not stored.is_warm_pool
    assert len([s for s in await pool.rows() if s.is_warm_pool]) <= 2


async def test_cancel_after_creation_commit_before_enqueue_is_recovered_on_restart(
    pool, monkeypatch
):
    enqueue = pool.queue.enqueue

    def cancel(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(pool.queue, "enqueue", cancel)
    with pytest.raises(asyncio.CancelledError):
        await pool.scheduler.run_once()
    pending = (await pool.rows())[0]
    assert pending.warm_state is None
    assert pool.queue.depth == 0
    monkeypatch.setattr(pool.queue, "enqueue", enqueue)
    restarted = WarmPoolScheduler(pool.config, pool.queue)
    assert await restarted.run_once() == {pool.profile.id: 0}
    assert pool.queue.is_pending(pending.id)
    assert len(await pool.rows()) == 1


async def test_cancel_worker_after_container_start_recovers_persisted_starting_session(
    pool, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()
    start = pool.driver.start

    async def pause_after_start(*args, **kwargs):
        endpoint = await start(*args, **kwargs)
        entered.set()
        await release.wait()
        return endpoint

    monkeypatch.setattr(pool.driver, "start", pause_after_start)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        monkeypatch.setattr(
            "app.managers.session.session.http_client_manager", SimpleNamespace(client=client)
        )
        await pool.queue.start()
        await pool.scheduler.run_once()
        await asyncio.wait_for(entered.wait(), 2)
        pending = (await pool.rows())[0]
        async with pool.factory() as db:
            session = await db.get(Session, pending.current_session_id)
            assert session.observed_state == SessionStatus.STARTING
            original_container = session.container_id
        # Cancellation while stopping, without waiting for the ten-second grace
        # timeout. This is the same state a killed process leaves in the DB.
        stopping = asyncio.create_task(pool.queue.stop())
        await asyncio.sleep(0)
        assert not pool.queue.is_running
        for worker in pool.queue._workers:
            worker.cancel()
        await asyncio.wait_for(stopping, 2)
        assert pool.queue.stats.active_workers == 0
        assert not pool.queue.is_pending(pending.id)
        await asyncio.wait_for(pool.queue._queue.join(), 1)

        monkeypatch.setattr(pool.driver, "start", start)
        restarted = WarmPoolScheduler(pool.config, pool.queue)
        await pool.queue.start()
        await restarted.run_once()
        await asyncio.wait_for(pool.queue._queue.join(), 2)
        async with pool.factory() as db:
            recovered = await db.get(Sandbox, pending.id)
            assert recovered.warm_state == WarmState.AVAILABLE.value
            session = await db.get(Session, recovered.current_session_id)
            assert session.container_id == original_container
            assert session.observed_state == SessionStatus.RUNNING
        assert len(pool.driver.create_calls) == 1


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_cancel_multi_container_warmup_before_id_commit_does_not_leak_runtimes(
    pool, monkeypatch, cleanup_fails
):
    pool.profile.containers = [
        ContainerSpec(name="ship", image="ship:latest", runtime_type="ship"),
        ContainerSpec(name="browser", image="gull:latest", runtime_type="gull"),
    ]
    pending = await pool.create()
    entered = asyncio.Event()
    ready = SessionManager._wait_for_multi_ready

    async def pause(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(SessionManager, "_wait_for_multi_ready", pause)
    task = asyncio.create_task(pool.queue._process_task(WarmupTask(pending, "warm-pool"), 0))
    try:
        await asyncio.wait_for(entered.wait(), 2)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    abandoned = set(pool.driver._containers)
    assert len(abandoned) == 2
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, pending)
        session = await db.get(Session, sandbox.current_session_id)
        assert session.observed_state == SessionStatus.STARTING
        assert session.container_id is None
        assert not session.containers

    # Discovery may return unrelated instances; scope must be checked before deletion.
    foreign = await pool.driver.create(
        Session(id="foreign-session", sandbox_id="foreign-sandbox"),
        pool.profile,
        SimpleNamespace(id="foreign-cargo"),
    )
    destroy = pool.driver.destroy_runtime_instance

    async def fail_cleanup(instance_id):
        raise RuntimeError("temporarily cannot remove orphan runtime")

    monkeypatch.setattr(SessionManager, "_wait_for_multi_ready", ready)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        monkeypatch.setattr(
            "app.managers.session.session.http_client_manager", SimpleNamespace(client=client)
        )
        if cleanup_fails:
            monkeypatch.setattr(pool.driver, "destroy_runtime_instance", fail_cleanup)
            await pool.queue._process_task(WarmupTask(pending, "warm-pool"), 0)
            assert abandoned.issubset(pool.driver._containers)
            assert len(pool.driver.create_multi_calls) == 1
            async with pool.factory() as db:
                sandbox = await db.get(Sandbox, pending)
                session = await db.get(Session, sandbox.current_session_id)
                assert session.observed_state == SessionStatus.STARTING
            monkeypatch.setattr(pool.driver, "destroy_runtime_instance", destroy)
        await pool.queue._process_task(WarmupTask(pending, "warm-pool"), 0)
    assert len(pool.driver._containers) == 3
    assert foreign in pool.driver._containers
    assert not abandoned.intersection(pool.driver._containers)
    assert len(pool.driver._networks) == 1
    async with pool.factory() as db:
        sandbox = await db.get(Sandbox, pending)
        assert sandbox.warm_state == WarmState.AVAILABLE.value


@pytest.mark.parametrize("seed", [17, 811])
async def test_sustained_rotation_claims_start_failures_and_runtime_loss_stay_bounded(
    pool, monkeypatch, seed
):
    rng = random.Random(seed)
    pool.profile.warm_pool_size = 2
    pool.profile.warm_rotate_ttl = 60
    pool.config.warmup_queue_workers = 2
    attempts = 0
    start = pool.driver.start

    async def sometimes_fail(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts % 7 == 0:
            raise RuntimeError("injected transient start failure")
        return await start(*args, **kwargs)

    monkeypatch.setattr(pool.driver, "start", sometimes_fail)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        monkeypatch.setattr(
            "app.managers.session.session.http_client_manager", SimpleNamespace(client=client)
        )
        await pool.queue.start()
        await pool.scheduler.run_once()
        await asyncio.wait_for(pool.queue._queue.join(), 2)
        held = await pool.claim()
        assert held is not None
        async with pool.factory() as db:
            held_runtime = (await db.get(Session, held.current_session_id)).container_id

        for tick in range(60):
            pool.advance(rng.randint(6, 16))
            # Multiple notifications/callers must serialize maintenance, not
            # create competing replacements from the same count snapshot.
            await asyncio.gather(*(pool.scheduler.run_once() for _ in range(3)))
            await asyncio.wait_for(pool.queue._queue.join(), 2)
            if tick % 7 == 0:
                transient_user = await pool.claim()
                if transient_user is not None:
                    async with pool.factory() as db:
                        await SandboxManager(pool.driver, db).delete(transient_user)
            if tick % 11 == 0:
                ready = [s for s in await pool.rows() if s.warm_state == "available"]
                if ready:
                    async with pool.factory() as db:
                        session = await db.get(Session, rng.choice(ready).current_session_id)
                        await pool.driver.destroy(session.container_id)
            rows = await pool.rows()
            warm = [s for s in rows if s.is_warm_pool]
            assert len(warm) <= 3
            assert len(pool.driver._containers) <= 4
            assert len(pool.driver._volumes) == len(rows) <= 4
            assert held_runtime in pool.driver._containers
            assert any(s.id == held.id and s.owner == "user" for s in rows)
        assert pool.queue.stats.failure_total >= 1
        assert pool.queue.stats.success_total >= 10
        # Every resource must still have a durable owner after repeated faults.
        async with pool.factory() as db:
            for sandbox in await pool.rows():
                cargo = await db.get(Cargo, sandbox.cargo_id)
                assert cargo is not None
                assert cargo.owner == sandbox.owner


async def test_user_starting_session_is_not_rewritten_by_warm_recovery(pool):
    sandbox_id = await pool.create(ready_for=300)
    claimed = await pool.claim()
    assert claimed.id == sandbox_id
    async with pool.factory() as db:
        session = await db.get(Session, claimed.current_session_id)
        session.observed_state = SessionStatus.STARTING
        session.endpoint = None
        await db.commit()
        from app.errors import SessionNotReadyError

        with pytest.raises(SessionNotReadyError):
            await SandboxManager(pool.driver, db).ensure_running(claimed)
        await db.refresh(session)
        assert session.observed_state == SessionStatus.STARTING
    assert not pool.driver.start_calls
    assert not pool.driver.destroy_calls
