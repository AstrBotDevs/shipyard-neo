"""Unit tests for SessionManager.

Focus: startup failure cleanup (no leaked containers, no stale endpoint persisted,
and correct error metadata).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, select

from app.config import ProfileConfig, ResourceSpec, Settings
from app.errors import SessionNotReadyError
from app.managers.session import SessionManager
from app.models.cargo import Cargo
from app.models.sandbox import Sandbox
from app.models.session import Session, SessionStatus
from tests.fakes import FakeDriver


class StartFailDriver(FakeDriver):
    async def start(self, container_id: str, *, runtime_port: int) -> str:
        raise RuntimeError("boom")


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(
        database={"url": "sqlite+aiosqlite:///:memory:"},
        driver={"type": "docker"},
        profiles=[
            ProfileConfig(
                id="python-default",
                image="ship:latest",
                resources=ResourceSpec(cpus=1.0, memory="1g"),
                capabilities=["filesystem", "shell", "python"],
                idle_timeout=1800,
                runtime_port=8123,
            ),
        ],
    )


@pytest.fixture
async def db_session(fake_settings: Settings):
    engine = create_async_engine(
        fake_settings.database.url,
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async_session_factory = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def profile(fake_settings: Settings) -> ProfileConfig:
    profile = fake_settings.get_profile("python-default")
    assert profile is not None
    return profile


@pytest.fixture
def cargo() -> Cargo:
    return Cargo(
        id="cargo-test-1",
        owner="test-user",
        managed=False,
    )


class TestSessionManagerEnsureRunning:
    async def test_start_failure_destroys_container_and_clears_runtime_fields(
        self,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = StartFailDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(id="sandbox-test-1", owner="test-user", profile_id=profile.id)
        db_session.add(sandbox)
        await db_session.commit()

        session = Session(
            id="sess-test-1",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.PENDING,
            observed_state=SessionStatus.PENDING,
        )
        db_session.add(session)
        await db_session.commit()

        with pytest.raises(RuntimeError, match="boom"):
            await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        # Refresh from DB to assert persisted values.
        result = await db_session.execute(select(Session).where(Session.id == session.id))
        refreshed = result.scalars().one()

        assert refreshed.observed_state == SessionStatus.FAILED
        assert refreshed.container_id is None
        assert refreshed.endpoint is None

        assert driver.destroy_calls == ["fake-container-1"]

    async def test_readiness_failure_destroys_container_does_not_persist_endpoint_and_sets_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = FakeDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(id="sandbox-test-2", owner="test-user", profile_id=profile.id)
        db_session.add(sandbox)
        await db_session.commit()

        session = Session(
            id="sess-test-2",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.PENDING,
            observed_state=SessionStatus.PENDING,
        )
        db_session.add(session)
        await db_session.commit()

        called: dict[str, str] = {}

        async def fake_wait_for_ready(
            endpoint: str,
            *,
            session_id: str,
            sandbox_id: str,
            **_kwargs,
        ) -> None:
            called["endpoint"] = endpoint
            called["session_id"] = session_id
            called["sandbox_id"] = sandbox_id
            raise SessionNotReadyError(
                message="Runtime failed to become ready",
                sandbox_id=sandbox_id,
                retry_after_ms=1000,
            )

        monkeypatch.setattr(manager, "_wait_for_ready", fake_wait_for_ready)

        with pytest.raises(SessionNotReadyError) as exc_info:
            await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        assert called["session_id"] == session.id
        assert called["sandbox_id"] == sandbox.id
        assert exc_info.value.details["sandbox_id"] == sandbox.id

        result = await db_session.execute(select(Session).where(Session.id == session.id))
        refreshed = result.scalars().one()

        assert refreshed.observed_state == SessionStatus.FAILED
        assert refreshed.container_id is None
        assert refreshed.endpoint is None

        assert driver.destroy_calls == ["fake-container-1"]
