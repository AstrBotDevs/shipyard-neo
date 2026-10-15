"""Shared pool harness with independent SQLite connections and a fake clock."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from app.config import ProfileConfig, Settings, WarmPoolConfig
from app.drivers.base import ContainerInfo, ContainerStatus
from app.managers.sandbox import SandboxManager
from app.models.sandbox import Sandbox
from app.models.session import Session, SessionStatus
from app.services.warm_pool.queue import WarmupQueue
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

    async def create(*, ready_for=None, profile_id=None):
        async with factory() as db:
            manager = SandboxManager(driver, db)
            sandbox = await manager.create_warm_sandbox(profile_id or profile.id)
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
        settings=settings,
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
