"""Unit tests for SessionManager.

Focus: startup failure cleanup (no leaked containers, no stale endpoint persisted,
and correct error metadata).

Phase 1.5: Added tests for proactive health probing (container dead detection
and recovery).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, select

from app.config import ProfileConfig, ResourceSpec, Settings
from app.drivers.base import ContainerInfo, ContainerStatus
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

    async def test_start_failure_preserves_container_reference_when_cleanup_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = StartFailDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(id="sandbox-start-cleanup", owner="test-user", profile_id=profile.id)
        session = Session(
            id="sess-start-cleanup",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.PENDING,
            observed_state=SessionStatus.PENDING,
        )
        db_session.add_all([sandbox, session])
        await db_session.commit()
        monkeypatch.setattr(
            driver,
            "destroy",
            AsyncMock(side_effect=RuntimeError("cleanup failed")),
        )
        monkeypatch.setattr(manager, "_wait_for_ready", AsyncMock(return_value=None))

        with pytest.raises(SessionNotReadyError, match="cleanup"):
            await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        await db_session.refresh(session)
        assert session.observed_state == SessionStatus.FAILED
        assert session.container_id == "fake-container-1"
        assert session.endpoint is None

    async def test_recovers_starting_session_when_container_is_missing(
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

        sandbox = Sandbox(id="sandbox-starting-missing", owner="test-user", profile_id=profile.id)
        session = Session(
            id="sess-starting-missing",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.STARTING,
            container_id="missing-container",
        )
        db_session.add_all([sandbox, session])
        await db_session.commit()

        driver.set_status_override(
            "missing-container",
            ContainerInfo(
                container_id="missing-container",
                status=ContainerStatus.NOT_FOUND,
            ),
        )
        monkeypatch.setattr(manager, "_wait_for_ready", AsyncMock(return_value=None))

        result = await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        assert result.observed_state == SessionStatus.RUNNING
        assert result.container_id == "fake-container-1"
        assert result.endpoint == "http://fake-host:8123"
        assert driver.destroy_calls == ["missing-container"]
        assert len(driver.create_calls) == 1
        assert driver.start_calls == [{"container_id": "fake-container-1", "runtime_port": 8123}]

    async def test_resumes_starting_session_when_container_is_created(
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

        sandbox = Sandbox(id="sandbox-starting-created", owner="test-user", profile_id=profile.id)
        session = Session(
            id="sess-starting-created",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.STARTING,
        )
        db_session.add_all([sandbox, session])
        await db_session.commit()

        session.container_id = await driver.create(session=session, profile=profile, cargo=cargo)
        driver.create_calls.clear()
        await db_session.commit()
        monkeypatch.setattr(manager, "_wait_for_ready", AsyncMock(return_value=None))

        result = await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        assert result.observed_state == SessionStatus.RUNNING
        assert result.container_id == "fake-container-1"
        assert result.endpoint == "http://fake-host:8123"
        assert driver.create_calls == []
        assert driver.destroy_calls == []
        assert driver.start_calls == [{"container_id": "fake-container-1", "runtime_port": 8123}]

    async def test_finishes_starting_session_when_container_is_running(
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

        sandbox = Sandbox(id="sandbox-starting-running", owner="test-user", profile_id=profile.id)
        session = Session(
            id="sess-starting-running",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.STARTING,
        )
        db_session.add_all([sandbox, session])
        await db_session.commit()

        session.container_id = await driver.create(session=session, profile=profile, cargo=cargo)
        await driver.start(session.container_id, runtime_port=8123)
        driver.create_calls.clear()
        driver.start_calls.clear()
        await db_session.commit()
        wait_for_ready = AsyncMock(return_value=None)
        monkeypatch.setattr(manager, "_wait_for_ready", wait_for_ready)

        result = await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        assert result.observed_state == SessionStatus.RUNNING
        assert result.container_id == "fake-container-1"
        assert result.endpoint == "http://fake-host:8123"
        assert driver.create_calls == []
        assert driver.start_calls == []
        wait_for_ready.assert_awaited_once_with(
            "http://fake-host:8123",
            session_id=session.id,
            sandbox_id=sandbox.id,
            runtime_type=session.runtime_type,
        )

    async def test_keeps_starting_session_when_runtime_probe_fails(
        self,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = FakeDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(
            id="sandbox-starting-probe-error", owner="test-user", profile_id=profile.id
        )
        session = Session(
            id="sess-starting-probe-error",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.STARTING,
            container_id="unreachable-container",
        )
        db_session.add_all([sandbox, session])
        await db_session.commit()
        driver.set_status_exception(RuntimeError("docker unavailable"))

        with pytest.raises(SessionNotReadyError, match="Session is starting"):
            await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        await db_session.refresh(session)
        assert session.observed_state == SessionStatus.STARTING
        assert session.container_id == "unreachable-container"
        assert driver.create_calls == []
        assert driver.destroy_calls == []

    async def test_preserves_starting_session_when_dead_container_cleanup_fails(
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

        sandbox = Sandbox(id="sandbox-starting-cleanup", owner="test-user", profile_id=profile.id)
        session = Session(
            id="sess-starting-cleanup",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.STARTING,
            container_id="dead-container",
        )
        db_session.add_all([sandbox, session])
        await db_session.commit()
        driver.set_status_override(
            "dead-container",
            ContainerInfo(
                container_id="dead-container",
                status=ContainerStatus.NOT_FOUND,
            ),
        )
        monkeypatch.setattr(
            driver,
            "destroy",
            AsyncMock(side_effect=RuntimeError("cleanup failed")),
        )
        monkeypatch.setattr(manager, "_wait_for_ready", AsyncMock(return_value=None))

        with pytest.raises(SessionNotReadyError, match="cleanup"):
            await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        await db_session.refresh(session)
        assert session.observed_state == SessionStatus.STARTING
        assert session.container_id == "dead-container"
        assert session.endpoint is None
        assert driver.create_calls == []

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


class TestSessionManagerHealthProbing:
    """Phase 1.5: Tests for proactive health probing."""

    async def test_ensure_running_probes_status_when_observed_running(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        """Verify that ensure_running calls driver.status when observed_state=RUNNING."""
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = FakeDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(id="sandbox-probe-1", owner="test-user", profile_id=profile.id)
        db_session.add(sandbox)
        await db_session.commit()

        # Simulate a session that's already running
        session = Session(
            id="sess-probe-1",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.RUNNING,
            container_id="fake-container-1",
            endpoint="http://fake-host:8123",
        )
        db_session.add(session)
        await db_session.commit()

        # Create the container in FakeDriver state so status() returns RUNNING
        driver._containers["fake-container-1"] = (
            type(driver._containers.get("x", None)).__class__(
                container_id="fake-container-1",
                session_id=session.id,
                profile_id=profile.id,
                cargo_id=cargo.id,
                status=ContainerStatus.RUNNING,
                endpoint="http://fake-host:8123",
            )
            if driver._containers
            else None
        )

        # Manually add container state
        from tests.fakes import FakeContainerState

        driver._containers["fake-container-1"] = FakeContainerState(
            container_id="fake-container-1",
            session_id=session.id,
            profile_id=profile.id,
            cargo_id=cargo.id,
            status=ContainerStatus.RUNNING,
            endpoint="http://fake-host:8123",
        )

        result = await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        # Should have called status() once
        assert len(driver.status_calls) == 1
        assert driver.status_calls[0]["container_id"] == "fake-container-1"

        # Session should still be ready
        assert result.is_ready

    async def test_ensure_running_recovers_from_exited_container(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        """Verify that when probe detects EXITED, session is reset and rebuilt."""
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = FakeDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(id="sandbox-probe-2", owner="test-user", profile_id=profile.id)
        db_session.add(sandbox)
        await db_session.commit()

        # Simulate a session that DB thinks is RUNNING
        session = Session(
            id="sess-probe-2",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.RUNNING,
            container_id="dead-container-1",
            endpoint="http://fake-host:8123",
        )
        db_session.add(session)
        await db_session.commit()

        # Override status to return EXITED (container is dead)
        driver.set_status_override(
            "dead-container-1",
            ContainerInfo(
                container_id="dead-container-1",
                status=ContainerStatus.EXITED,
                exit_code=137,  # OOM killed
            ),
        )

        # Mock _wait_for_ready to succeed (don't actually wait for HTTP)
        async def fake_wait_for_ready(*args, **kwargs):
            pass

        monkeypatch.setattr(manager, "_wait_for_ready", fake_wait_for_ready)

        result = await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        # Should have called status() once during probe
        assert len(driver.status_calls) >= 1

        # Should have attempted to destroy the dead container
        assert "dead-container-1" in driver.destroy_calls

        # Should have created a new container
        assert len(driver.create_calls) == 1

        # Session should now be running with a new container
        assert result.observed_state == SessionStatus.RUNNING
        assert result.container_id is not None
        assert result.container_id != "dead-container-1"

    async def test_ensure_running_recovers_from_not_found_container(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        """Verify that when probe detects NOT_FOUND, session is reset and rebuilt."""
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = FakeDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(id="sandbox-probe-3", owner="test-user", profile_id=profile.id)
        db_session.add(sandbox)
        await db_session.commit()

        # Simulate a session that DB thinks is RUNNING but container is gone
        session = Session(
            id="sess-probe-3",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.RUNNING,
            container_id="vanished-container-1",
            endpoint="http://fake-host:8123",
        )
        db_session.add(session)
        await db_session.commit()

        # Override status to return NOT_FOUND (container vanished)
        driver.set_status_override(
            "vanished-container-1",
            ContainerInfo(
                container_id="vanished-container-1",
                status=ContainerStatus.NOT_FOUND,
            ),
        )

        # Mock _wait_for_ready to succeed
        async def fake_wait_for_ready(*args, **kwargs):
            pass

        monkeypatch.setattr(manager, "_wait_for_ready", fake_wait_for_ready)

        result = await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        # Should have created a new container
        assert len(driver.create_calls) == 1

        # Session should now be running with a new container
        assert result.observed_state == SessionStatus.RUNNING
        assert result.container_id is not None
        assert result.container_id != "vanished-container-1"

    async def test_dead_container_cleanup_failure_preserves_runtime_reference(
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

        sandbox = Sandbox(id="sandbox-probe-cleanup", owner="test-user", profile_id=profile.id)
        session = Session(
            id="sess-probe-cleanup",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.RUNNING,
            container_id="dead-container",
            endpoint="http://dead-runtime:8123",
        )
        db_session.add_all([sandbox, session])
        await db_session.commit()
        driver.set_status_override(
            "dead-container",
            ContainerInfo(
                container_id="dead-container",
                status=ContainerStatus.EXITED,
            ),
        )
        monkeypatch.setattr(
            driver,
            "destroy",
            AsyncMock(side_effect=RuntimeError("cleanup failed")),
        )
        monkeypatch.setattr(manager, "_wait_for_ready", AsyncMock(return_value=None))

        with pytest.raises(SessionNotReadyError, match="cleanup"):
            await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        await db_session.refresh(session)
        assert session.observed_state == SessionStatus.RUNNING
        assert session.container_id == "dead-container"
        assert session.endpoint == "http://dead-runtime:8123"

    async def test_ensure_running_degrades_gracefully_when_docker_unreachable(
        self,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        """Verify that when driver.status raises, we degrade to trusting DB state."""
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = FakeDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(id="sandbox-probe-4", owner="test-user", profile_id=profile.id)
        db_session.add(sandbox)
        await db_session.commit()

        # Simulate a session that DB thinks is RUNNING
        session = Session(
            id="sess-probe-4",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.RUNNING,
            observed_state=SessionStatus.RUNNING,
            container_id="unreachable-container-1",
            endpoint="http://fake-host:8123",
        )
        db_session.add(session)
        await db_session.commit()

        # Set exception to simulate Docker daemon unreachable
        driver.set_status_exception(ConnectionError("Docker daemon unreachable"))

        # Also need to set is_ready properly - create a container state
        from tests.fakes import FakeContainerState

        driver._containers["unreachable-container-1"] = FakeContainerState(
            container_id="unreachable-container-1",
            session_id=session.id,
            profile_id=profile.id,
            cargo_id=cargo.id,
            status=ContainerStatus.RUNNING,
            endpoint="http://fake-host:8123",
        )

        # Since is_ready checks observed_state and endpoint, this should return the session
        result = await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        # Should have attempted to call status()
        assert len(driver.status_calls) == 1

        # Should NOT have created a new container (degrade to trusting DB)
        assert len(driver.create_calls) == 0

        # Session should still appear ready (trusted DB state)
        assert result.observed_state == SessionStatus.RUNNING

    async def test_no_probe_when_session_pending(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
        fake_settings: Settings,
        profile: ProfileConfig,
        cargo: Cargo,
    ):
        """Verify that no probe happens when session is PENDING."""
        with patch("app.managers.session.session.get_settings", return_value=fake_settings):
            driver = FakeDriver()
            manager = SessionManager(driver=driver, db_session=db_session)

        sandbox = Sandbox(id="sandbox-probe-5", owner="test-user", profile_id=profile.id)
        db_session.add(sandbox)
        await db_session.commit()

        # Session in PENDING state
        session = Session(
            id="sess-probe-5",
            sandbox_id=sandbox.id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.PENDING,
            observed_state=SessionStatus.PENDING,
            container_id=None,
            endpoint=None,
        )
        db_session.add(session)
        await db_session.commit()

        # Mock _wait_for_ready to succeed
        async def fake_wait_for_ready(*args, **kwargs):
            pass

        monkeypatch.setattr(manager, "_wait_for_ready", fake_wait_for_ready)

        await manager.ensure_running(session=session, cargo=cargo, profile=profile)

        # Should NOT have called status() because session is PENDING
        assert len(driver.status_calls) == 0
