"""WarmPoolScheduler - Event-driven refill with periodic reconciliation.

Responsibilities (from §4.5):
1. Periodically scan profiles with warm_pool_size > 0
2. Count available warm instances
3. Recover dropped/failed warmup work and replenish after claims
4. Prepare bounded replacements before rotating instances past warm_rotate_at
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import or_, update
from sqlmodel import select

from app.drivers.base import ContainerStatus
from app.models.sandbox import Sandbox, WarmState
from app.models.session import Session, SessionStatus
from app.utils.datetime import utcnow

if TYPE_CHECKING:
    from app.config import WarmPoolConfig
    from app.services.warm_pool.queue import WarmupQueue

logger = structlog.get_logger()


class WarmPoolScheduler:
    """Scheduler for warm pool maintenance.

    Periodically checks each profile's warm pool and:
    - Wakes on claims and replenishes to target size
    - Prepares at most one replacement per profile ahead of rotation
    - Rotates instances when warm_rotate_at <= now
    """

    def __init__(
        self,
        config: "WarmPoolConfig",
        warmup_queue: "WarmupQueue",
    ) -> None:
        self._config = config
        self._queue = warmup_queue
        self._log = logger.bind(service="warm_pool_scheduler")

        self._running = False
        self._task: asyncio.Task | None = None
        self._run_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._retry_after: dict[str, float] = {}
        self._retry_cursor: dict[str, str] = {}
        self._create_retry_after: dict[str, float] = {}
        self._profile_offset = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def request_maintenance(self) -> None:
        """Coalesce refill requests without blocking the caller."""
        self._wake.set()

    async def start(self) -> None:
        """Start background maintenance loop."""
        if self._running:
            self._log.warning("warm_pool_scheduler.already_running")
            return

        self._running = True
        self._task = asyncio.create_task(
            self._background_loop(),
            name="warm-pool-scheduler",
        )
        self._log.info(
            "warm_pool_scheduler.started",
            interval_seconds=self._config.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop background maintenance loop gracefully."""
        if not self._running:
            return

        self._log.info("warm_pool_scheduler.stopping")
        self._running = False

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self._log.info("warm_pool_scheduler.stopped")

    async def run_once(self) -> dict[str, int]:
        """Execute one pool maintenance cycle.

        Returns:
            Dict of profile_id -> number of sandboxes created
        """
        async with self._run_lock:
            return await self._run_cycle()

    async def _run_cycle(self) -> dict[str, int]:
        """Internal: Execute one pool maintenance cycle."""
        from app.config import get_settings

        settings = get_settings()
        results: dict[str, int] = {}

        # Find all profiles with warm pool enabled
        warm_profiles = [p for p in settings.profiles if p.warm_pool_size > 0]

        if not warm_profiles:
            return results

        # A small queue must not always give the first profile its only slot.
        offset = self._profile_offset % len(warm_profiles)
        warm_profiles = warm_profiles[offset:] + warm_profiles[:offset]
        self._profile_offset = (offset + 1) % len(warm_profiles)

        self._log.info(
            "warm_pool.cycle.start",
            profiles=len(warm_profiles),
        )

        for profile in warm_profiles:
            try:
                created = await self._maintain_profile(profile)
                results[profile.id] = created
            except Exception as exc:
                self._log.exception(
                    "warm_pool.cycle.profile_error",
                    profile_id=profile.id,
                    error=str(exc),
                )

        total_created = sum(results.values())
        self._log.info(
            "warm_pool.cycle.complete",
            total_created=total_created,
            profiles=results,
        )

        return results

    async def _reconcile_profile_runtime_state(self, profile_id: str) -> int:
        """Reconcile DB warm-pool state with actual runtime state.

        For warm sandboxes marked as available, the DB may drift from reality after
        driver/runtime restarts. This periodically re-validates those rows against
        the runtime backend and downgrades broken entries back to pending so they
        can be re-enqueued for warmup.

        Returns:
            Number of warm sandboxes requeued for recovery.
        """
        from app.api.dependencies import get_driver
        from app.db.session import get_async_session

        async with get_async_session() as db:
            result = await db.execute(
                select(Sandbox, Session)
                .outerjoin(Session, Session.id == Sandbox.current_session_id)
                .where(
                    Sandbox.deleted_at.is_(None),
                    Sandbox.is_warm_pool.is_(True),
                    Sandbox.warm_state == WarmState.AVAILABLE.value,
                    Sandbox.profile_id == profile_id,
                    or_(Sandbox.warm_rotate_at.is_(None), Sandbox.warm_rotate_at > utcnow()),
                )
            )
            available_rows = result.all()

        if not available_rows:
            return 0

        driver = get_driver()
        stale_rows: list[tuple[Sandbox, Session | None]] = []
        requeued = 0

        async def probe(row) -> bool:
            sandbox, session = row
            if self._queue.is_pending(sandbox.id):
                return True
            if session is None:
                return False
            return await self._is_session_runtime_healthy(driver, session)

        # Probe independent runtimes concurrently, with the same bounded fanout
        # as warmup workers. Batch tasks are drained before applying any state
        # changes; cancellation must not leave background probes behind.
        concurrency = max(1, self._config.warmup_queue_workers)
        for offset in range(0, len(available_rows), concurrency):
            batch = available_rows[offset : offset + concurrency]
            outcomes = await asyncio.gather(*(probe(row) for row in batch), return_exceptions=True)
            for (sandbox, session), runtime_ok in zip(batch, outcomes, strict=True):
                if isinstance(runtime_ok, BaseException):
                    raise runtime_ok
                if runtime_ok:
                    self._log.debug(
                        "warm_pool.runtime_state_healthy",
                        sandbox_id=sandbox.id,
                        profile_id=profile_id,
                        session_id=sandbox.current_session_id,
                        current_session_id=sandbox.current_session_id,
                        warm_state=sandbox.warm_state,
                    )
                else:
                    stale_rows.append((sandbox, session))

        if not stale_rows:
            return 0

        recovered: list[tuple[str, str]] = []
        async with get_async_session() as db:
            for sandbox, session in stale_rows:
                # The probe ran outside the transaction. A user may have
                # claimed this instance, or a worker may have replaced its
                # session in the meantime. Only reset the version we probed.
                result = await db.execute(
                    update(Sandbox)
                    .where(
                        Sandbox.id == sandbox.id,
                        Sandbox.deleted_at.is_(None),
                        Sandbox.is_warm_pool.is_(True),
                        Sandbox.warm_state == WarmState.AVAILABLE.value,
                        Sandbox.current_session_id == sandbox.current_session_id,
                        Sandbox.warm_ready_at == sandbox.warm_ready_at,
                        # A slow runtime probe may cross the rotation deadline.
                        # Leave expired instances for retirement, not recovery.
                        or_(Sandbox.warm_rotate_at.is_(None), Sandbox.warm_rotate_at > utcnow()),
                    )
                    .values(warm_state=None, warm_ready_at=None, warm_rotate_at=None)
                )
                if not result.rowcount:
                    continue
                if session is not None:
                    await db.execute(
                        update(Session)
                        .where(Session.id == session.id)
                        .values(
                            observed_state=SessionStatus.STOPPED,
                            endpoint=None,
                            last_observed_at=utcnow(),
                        )
                    )
                recovered.append((sandbox.id, sandbox.owner))
            await db.commit()

        # Publish work only after the pending state has been committed.
        for sandbox_id, owner in recovered:
            if self._queue.enqueue(sandbox_id=sandbox_id, owner=owner, background=True):
                requeued += 1

        self._log.info(
            "warm_pool.runtime_reconcile.complete",
            profile_id=profile_id,
            checked=len(available_rows),
            stale=len(recovered),
            requeued=requeued,
        )
        return requeued

    async def _is_session_runtime_healthy(self, driver, session: Session) -> bool:
        """Check whether a session still has a live runtime behind it."""
        if session.containers:
            expected_container_names = [c.get("name") for c in session.containers]
            expected_container_ids = [c.get("container_id") for c in session.containers]
            try:
                instances = await driver.list_runtime_instances(
                    labels={
                        "bay.session_id": session.id,
                    }
                )
            except Exception as exc:
                self._log.warning(
                    "warm_pool.runtime_probe_failed",
                    session_id=session.id,
                    mode="multi",
                    expected_container_names=expected_container_names,
                    expected_container_ids=expected_container_ids,
                    error=str(exc),
                )
                return True

            healthy = any(instance.state == ContainerStatus.RUNNING.value for instance in instances)
            self._log.debug(
                "warm_pool.runtime_probe_result",
                session_id=session.id,
                mode="multi",
                expected_container_names=expected_container_names,
                expected_container_ids=expected_container_ids,
                discovered_runtime_ids=[instance.id for instance in instances],
                discovered_runtime_states={instance.id: instance.state for instance in instances},
                healthy=healthy,
            )
            return healthy

        if session.container_id is None:
            self._log.debug(
                "warm_pool.runtime_probe_result",
                session_id=session.id,
                mode="single",
                container_id=None,
                healthy=False,
                reason="missing_container_id",
            )
            return False

        try:
            info = await driver.status(session.container_id, runtime_port=None)
        except Exception as exc:
            self._log.warning(
                "warm_pool.runtime_probe_failed",
                session_id=session.id,
                container_id=session.container_id,
                mode="single",
                error=str(exc),
            )
            return True

        healthy = info.status == ContainerStatus.RUNNING
        self._log.debug(
            "warm_pool.runtime_probe_result",
            session_id=session.id,
            mode="single",
            container_id=session.container_id,
            discovered_status=info.status.value,
            healthy=healthy,
        )
        return healthy

    def _rotation_lead_seconds(self, profile) -> float:
        """Prepare early using observed queue/start cost, without changing TTL.

        Keep at least half of a warm instance's lifetime outside the preparation
        window, so short TTLs cannot cause continuous replacement creation.
        """
        interval = max(1.0, self._config.interval_seconds)
        estimate = self._queue.estimated_warmup_seconds(profile.id)
        return min(max(0.0, profile.warm_rotate_ttl / 2), interval + estimate * 1.5)

    async def _maintain_profile(self, profile) -> int:
        """Recover lost work, retire expired instances and replenish the pool.

        At most one extra instance per profile may be prepared for rotation.
        Claimed sandboxes are excluded by every maintenance write.
        """
        from app.db.session import get_async_session

        await self._reconcile_profile_runtime_state(profile.id)
        now = utcnow()

        async with get_async_session() as db:
            # Conditional UPDATE competes safely with the claim UPDATE. An
            # expired instance still leaves the pool at its original deadline,
            # even if its replacement failed to start.
            await db.execute(
                update(Sandbox)
                .where(
                    Sandbox.deleted_at.is_(None),
                    Sandbox.is_warm_pool.is_(True),
                    Sandbox.warm_state == WarmState.AVAILABLE.value,
                    Sandbox.profile_id == profile.id,
                    Sandbox.warm_rotate_at <= now,
                )
                .values(warm_state=WarmState.RETIRING.value)
            )
            await db.commit()
            result = await db.execute(
                select(Sandbox).where(
                    Sandbox.deleted_at.is_(None),
                    Sandbox.is_warm_pool.is_(True),
                    Sandbox.warm_state == WarmState.RETIRING.value,
                    Sandbox.profile_id == profile.id,
                )
            )
            retiring = [(sandbox.id, sandbox.owner) for sandbox in result.scalars().all()]

        # Retry previously failed retirements as well, even when no refill is
        # necessary. Delete before creating to limit temporary resource use.
        for sandbox_id, owner in retiring:
            await self._retire_warm_sandbox(sandbox_id, owner)

        async with get_async_session() as db:
            result = await db.execute(
                select(Sandbox)
                .where(
                    Sandbox.deleted_at.is_(None),
                    Sandbox.is_warm_pool.is_(True),
                    Sandbox.profile_id == profile.id,
                )
                .order_by(Sandbox.created_at.asc(), Sandbox.id.asc())
            )
            pool = result.scalars().all()

        available = [s for s in pool if s.warm_state == WarmState.AVAILABLE.value]
        pending = [s for s in pool if s.warm_state is None]
        clock_now = time.monotonic()
        if clock_now >= self._retry_after.get(profile.id, 0.0):
            # DB pending rows are durable recovery records, not proof that the
            # in-memory queue still owns the work. Retry dropped/failed work at
            # most once per scan interval, even during a burst of claim events.
            self._retry_after[profile.id] = clock_now + max(1.0, self._config.interval_seconds)
            cursor = self._retry_cursor.get(profile.id)
            for index, sandbox in enumerate(pending):
                if sandbox.id == cursor:
                    # Resume after the last admitted task. Otherwise a failed
                    # oldest row can monopolize every retry in a small queue.
                    pending = pending[index + 1 :] + pending[: index + 1]
                    break
            for sandbox in pending:
                if not self._queue.is_pending(sandbox.id):
                    accepted = self._queue.enqueue(
                        sandbox_id=sandbox.id, owner=sandbox.owner, background=True
                    )
                    if not accepted:
                        break
                    self._retry_cursor[profile.id] = sandbox.id

        prepare_before = now + timedelta(seconds=self._rotation_lead_seconds(profile))
        rotating_soon = any(
            s.warm_rotate_at is not None and s.warm_rotate_at <= prepare_before for s in available
        )
        target = profile.warm_pool_size + int(rotating_soon)
        # Include failed deletions in the resource cap. They must not cause
        # unbounded growth on subsequent cycles.
        capacity = max(0, profile.warm_pool_size + 1 - len(pool))
        deficit = min(capacity, max(0, target - len(available) - len(pending)))
        if not deficit or clock_now < self._create_retry_after.get(profile.id, 0.0):
            return 0

        self._log.info(
            "warm_pool.replenishing",
            profile_id=profile.id,
            deficit=deficit,
            available=len(available),
            pending=len(pending),
            preparing_rotation=rotating_soon,
        )
        created_count = 0
        for _ in range(deficit):
            try:
                sandbox_id = await self._create_warm_sandbox(profile)
                if sandbox_id:
                    created_count += 1
            except Exception as exc:
                self._create_retry_after[profile.id] = time.monotonic() + max(
                    1.0, self._config.interval_seconds
                )
                self._log.warning(
                    "warm_pool.create_failed",
                    profile_id=profile.id,
                    error=str(exc),
                )
                break
        return created_count

    async def _create_warm_sandbox(self, profile) -> str | None:
        """Create a warm sandbox and enqueue it for warmup.

        Returns sandbox_id if created, None if failed.
        """
        from app.api.dependencies import get_driver
        from app.db.session import get_async_session
        from app.managers.sandbox import SandboxManager

        async with get_async_session() as db:
            manager = SandboxManager(driver=get_driver(), db_session=db)
            sandbox = await manager.create_warm_sandbox(
                profile_id=profile.id,
                warm_rotate_ttl=profile.warm_rotate_ttl,
            )

            self._log.info(
                "warm_pool.sandbox_created",
                sandbox_id=sandbox.id,
                profile_id=profile.id,
            )

            # Enqueue warmup via the shared queue (§2.5.1: pool + create share same queue)
            self._queue.enqueue(
                sandbox_id=sandbox.id,
                owner=sandbox.owner,
                background=True,
            )

            return sandbox.id

    async def _retire_warm_sandbox(self, sandbox_id: str, owner: str) -> None:
        """Retire a warm sandbox (stop and delete)."""
        from app.api.dependencies import get_driver
        from app.db.session import get_async_session
        from app.managers.sandbox import SandboxManager

        async with get_async_session() as db:
            manager = SandboxManager(driver=get_driver(), db_session=db)
            try:
                sandbox = await manager.get(sandbox_id, owner)
                if not sandbox.is_warm_pool or sandbox.warm_state != WarmState.RETIRING.value:
                    return
                await manager.delete(
                    sandbox,
                    delete_source="warm_pool.scheduler.retire",
                )
                self._log.info(
                    "warm_pool.sandbox_retired",
                    sandbox_id=sandbox_id,
                )
            except Exception as exc:
                self._log.warning(
                    "warm_pool.retire_error",
                    sandbox_id=sandbox_id,
                    error=str(exc),
                )

    async def _background_loop(self) -> None:
        """Internal background loop.

        Note:
        - If run_on_startup is enabled, lifecycle already executed one cycle.
          Sleep before the first loop cycle to avoid immediate duplicate replenishment.
        """
        first_iteration = True

        while self._running:
            should_sleep = (first_iteration and self._config.run_on_startup) or (
                not first_iteration
            )
            if should_sleep:
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=max(1.0, self._config.interval_seconds)
                    )
                except TimeoutError:
                    pass
                except asyncio.CancelledError:
                    break

            # Clear before running so a claim during maintenance schedules one
            # more pass instead of being lost. Many notifications coalesce.
            self._wake.clear()
            first_iteration = False

            try:
                await self.run_once()
            except Exception as exc:
                self._log.exception(
                    "warm_pool_scheduler.cycle_error",
                    error=str(exc),
                )
