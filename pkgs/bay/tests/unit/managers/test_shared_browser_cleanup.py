"""Unit test for shared Gull cleanup on sandbox delete.

Verifies that SandboxManager.delete() notifies the shared Gull Service
when the sandbox's profile uses browser:shared.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, select

from app.config import BrowserServiceConfig, ContainerSpec, ProfileConfig, Settings
from app.managers.sandbox import SandboxManager
from tests.fakes import FakeDriver


@pytest.fixture
def shared_browser_settings() -> Settings:
    return Settings(
        database={"url": "sqlite+aiosqlite:///:memory:"},
        driver={"type": "docker"},
        browser_service=BrowserServiceConfig(
            enabled=True, endpoint="http://gull:8115",
        ),
        profiles=[
            ProfileConfig(
                id="browser-shared",
                browser="shared",
                containers=[
                    ContainerSpec(
                        name="ship", image="ship:latest",
                        runtime_type="ship", runtime_port=8123,
                        capabilities=["python", "shell", "filesystem", "browser"],
                    ),
                ],
            ),
        ],
    )


@pytest.fixture
async def db_session(shared_browser_settings: Settings):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def fake_driver() -> FakeDriver:
    return FakeDriver()


@pytest.fixture
def sandbox_mgr(fake_driver, db_session, shared_browser_settings):
    with patch(
        "app.managers.sandbox.sandbox.get_settings",
        return_value=shared_browser_settings,
    ):
        with patch(
            "app.managers.cargo.cargo.get_settings",
            return_value=shared_browser_settings,
        ):
            yield SandboxManager(driver=fake_driver, db_session=db_session)


class TestSharedBrowserCleanupOnDelete:
    """Sandbox delete with browser:shared triggers Gull session cleanup."""

    async def test_delete_calls_destroy_session(
        self, sandbox_mgr: SandboxManager,
    ):
        """Deleting a shared-browser sandbox calls SharedGullAdapter.destroy_session."""
        sandbox = await sandbox_mgr.create(
            owner="test", profile_id="browser-shared",
        )

        with patch(
            "app.adapters.shared_gull.SharedGullAdapter.destroy_session",
            new_callable=AsyncMock,
        ) as mock_destroy:
            await sandbox_mgr.delete(sandbox, delete_source="test")
            mock_destroy.assert_awaited_once_with(sandbox.id)

    async def test_delete_succeeds_even_if_destroy_fails(
        self, sandbox_mgr: SandboxManager, db_session: AsyncSession,
    ):
        """If destroy_session raises, delete still completes."""
        from app.models.sandbox import Sandbox as SandboxModel

        sandbox = await sandbox_mgr.create(
            owner="test", profile_id="browser-shared",
        )

        with patch(
            "app.adapters.shared_gull.SharedGullAdapter.destroy_session",
            side_effect=ConnectionError("gull unreachable"),
        ):
            # Must not raise
            await sandbox_mgr.delete(sandbox, delete_source="test")

        # Verify sandbox was actually soft-deleted (query DB directly)
        result = await db_session.execute(
            select(SandboxModel).where(SandboxModel.id == sandbox.id)
        )
        s = result.scalars().first()
        assert s is not None
        assert s.deleted_at is not None
