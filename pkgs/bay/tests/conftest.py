"""Test configuration and fixtures."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Get test settings with in-memory SQLite."""
    return Settings(
        database={"url": "sqlite+aiosqlite:///:memory:"},
        driver={"type": "docker"},
    )


@pytest.fixture
async def db_session(test_settings: Settings):
    """Create test database session."""
    engine = create_async_engine(
        test_settings.database.url,
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
