"""Unit tests for SandboxManager warm pool methods."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.managers.sandbox import SandboxManager
from app.models.sandbox import Sandbox, WarmState
from app.utils.datetime import utcnow
from tests.fakes import FakeDriver


@pytest.fixture
async def db_session():
    """Create test database session with in-memory SQLite."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
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
def driver():
    return FakeDriver()


@pytest.fixture
def sandbox_mgr(driver, db_session):
    return SandboxManager(driver=driver, db_session=db_session)


class TestCreateWarmSandbox:
    """Tests for create_warm_sandbox."""

    @pytest.mark.asyncio
    async def test_create_warm_sandbox_basic(self, sandbox_mgr, db_session):
        """Should create a sandbox with warm pool metadata."""
        sandbox = await sandbox_mgr.create_warm_sandbox(
            profile_id="python-default",
            warm_rotate_ttl=3600,
        )

        assert sandbox.is_warm_pool is True
        assert sandbox.warm_state is None  # Not yet available
        assert sandbox.warm_source_profile_id == "python-default"
        assert sandbox.owner == "warm-pool"
        assert sandbox.expires_at is None  # Warm pool instances don't expire via TTL
        assert sandbox.cargo_id is not None

    @pytest.mark.asyncio
    async def test_create_warm_sandbox_custom_owner(self, sandbox_mgr):
        """Should support custom owner scope."""
        sandbox = await sandbox_mgr.create_warm_sandbox(
            profile_id="python-default",
            owner="custom-owner",
        )

        assert sandbox.owner == "custom-owner"


class TestMarkWarmAvailable:
    """Tests for mark_warm_available."""

    @pytest.mark.asyncio
    async def test_mark_available_after_warmup(self, sandbox_mgr, db_session):
        """After warmup, sandbox should be marked available with rotation time."""
        sandbox = await sandbox_mgr.create_warm_sandbox(
            profile_id="python-default",
            warm_rotate_ttl=1800,
        )

        await sandbox_mgr.mark_warm_available(sandbox.id, warm_rotate_ttl=1800)

        # Refetch
        from sqlmodel import select

        result = await db_session.execute(select(Sandbox).where(Sandbox.id == sandbox.id))
        updated = result.scalars().first()

        assert updated.warm_state == WarmState.AVAILABLE.value
        assert updated.warm_ready_at is not None
        assert updated.warm_rotate_at is not None
        # warm_rotate_at should be approximately warm_ready_at + 1800s
        delta = (updated.warm_rotate_at - updated.warm_ready_at).total_seconds()
        assert abs(delta - 1800) < 2  # Allow 2s tolerance


class TestClaimWarmSandbox:
    """Tests for claim_warm_sandbox."""

    @pytest.mark.asyncio
    async def test_claim_success(self, sandbox_mgr, db_session):
        """Should successfully claim an available warm sandbox."""
        # Create and mark available
        sandbox = await sandbox_mgr.create_warm_sandbox(
            profile_id="python-default",
        )
        await sandbox_mgr.mark_warm_available(sandbox.id, warm_rotate_ttl=1800)

        # Claim
        claimed = await sandbox_mgr.claim_warm_sandbox(
            owner="user-1",
            profile_id="python-default",
            ttl=300,
        )

        assert claimed is not None
        assert claimed.id == sandbox.id
        assert claimed.owner == "user-1"
        assert claimed.is_warm_pool is False
        assert claimed.warm_state == WarmState.CLAIMED.value
        assert claimed.warm_claimed_at is not None
        assert claimed.expires_at is not None

    @pytest.mark.asyncio
    async def test_claim_no_available(self, sandbox_mgr):
        """Should return None when no warm sandbox available."""
        claimed = await sandbox_mgr.claim_warm_sandbox(
            owner="user-1",
            profile_id="python-default",
        )

        assert claimed is None

    @pytest.mark.asyncio
    async def test_claim_wrong_profile(self, sandbox_mgr):
        """Should return None when profile doesn't match."""
        sandbox = await sandbox_mgr.create_warm_sandbox(
            profile_id="python-default",
        )
        await sandbox_mgr.mark_warm_available(sandbox.id)

        claimed = await sandbox_mgr.claim_warm_sandbox(
            owner="user-1",
            profile_id="python-data",  # Different profile
        )

        assert claimed is None

    @pytest.mark.asyncio
    async def test_claim_retiring_not_claimable(self, sandbox_mgr, db_session):
        """Retiring sandbox should not be claimable."""
        sandbox = await sandbox_mgr.create_warm_sandbox(
            profile_id="python-default",
        )
        await sandbox_mgr.mark_warm_available(sandbox.id)
        await sandbox_mgr.mark_warm_retiring(sandbox.id)

        claimed = await sandbox_mgr.claim_warm_sandbox(
            owner="user-1",
            profile_id="python-default",
        )

        assert claimed is None

    @pytest.mark.asyncio
    async def test_claim_with_no_ttl(self, sandbox_mgr):
        """Claimed sandbox with no TTL should have expires_at=None."""
        sandbox = await sandbox_mgr.create_warm_sandbox(
            profile_id="python-default",
        )
        await sandbox_mgr.mark_warm_available(sandbox.id)

        claimed = await sandbox_mgr.claim_warm_sandbox(
            owner="user-1",
            profile_id="python-default",
            ttl=None,
        )

        assert claimed is not None
        assert claimed.expires_at is None

    @pytest.mark.asyncio
    async def test_claim_picks_oldest_ready(self, sandbox_mgr, db_session):
        """Should prefer the oldest warm_ready_at sandbox."""
        import asyncio

        # Create two warm sandboxes
        sb1 = await sandbox_mgr.create_warm_sandbox(profile_id="python-default")
        await sandbox_mgr.mark_warm_available(sb1.id)

        # Small delay so warm_ready_at differs
        await asyncio.sleep(0.01)

        sb2 = await sandbox_mgr.create_warm_sandbox(profile_id="python-default")
        await sandbox_mgr.mark_warm_available(sb2.id)

        # Claim should pick sb1 (oldest)
        claimed = await sandbox_mgr.claim_warm_sandbox(
            owner="user-1",
            profile_id="python-default",
        )

        assert claimed is not None
        assert claimed.id == sb1.id


class TestMarkWarmRetiring:
    """Tests for mark_warm_retiring."""

    @pytest.mark.asyncio
    async def test_mark_retiring(self, sandbox_mgr, db_session):
        """Should mark available sandbox as retiring."""
        sandbox = await sandbox_mgr.create_warm_sandbox(profile_id="python-default")
        await sandbox_mgr.mark_warm_available(sandbox.id)

        await sandbox_mgr.mark_warm_retiring(sandbox.id)

        from sqlmodel import select

        result = await db_session.execute(select(Sandbox).where(Sandbox.id == sandbox.id))
        updated = result.scalars().first()
        assert updated.warm_state == WarmState.RETIRING.value

    @pytest.mark.asyncio
    async def test_mark_retiring_already_claimed_noop(self, sandbox_mgr, db_session):
        """Should be a no-op if sandbox is already claimed."""
        sandbox = await sandbox_mgr.create_warm_sandbox(profile_id="python-default")
        await sandbox_mgr.mark_warm_available(sandbox.id)

        # Claim it
        await sandbox_mgr.claim_warm_sandbox(
            owner="user-1",
            profile_id="python-default",
        )

        # Try to retire (should be no-op since it's claimed and no longer warm_pool)
        await sandbox_mgr.mark_warm_retiring(sandbox.id)

        # Verify state is still claimed
        from sqlmodel import select

        result = await db_session.execute(select(Sandbox).where(Sandbox.id == sandbox.id))
        updated = result.scalars().first()
        assert updated.warm_state == WarmState.CLAIMED.value


class TestListExcludesWarmPool:
    """Tests that list() excludes warm pool sandboxes."""

    @pytest.mark.asyncio
    async def test_list_excludes_warm_sandboxes(self, sandbox_mgr, db_session):
        """Warm pool sandboxes should not appear in user list."""
        # Create a normal sandbox
        normal = await sandbox_mgr.create(owner="user-1", profile_id="python-default")

        # Create a warm sandbox
        warm = await sandbox_mgr.create_warm_sandbox(profile_id="python-default")
        await sandbox_mgr.mark_warm_available(warm.id)

        # List for the warm-pool owner
        items, _ = await sandbox_mgr.list(owner="warm-pool")
        warm_ids = {item.sandbox.id for item in items}
        assert warm.id not in warm_ids

        # List for user-1 should show normal sandbox
        items, _ = await sandbox_mgr.list(owner="user-1")
        ids = {item.sandbox.id for item in items}
        assert normal.id in ids
