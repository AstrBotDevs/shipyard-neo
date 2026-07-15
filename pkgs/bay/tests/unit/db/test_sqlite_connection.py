"""SQLite connection settings used by Bay's persistent database."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.config import DatabaseConfig, Settings
from app.db import session as db_session


@pytest.mark.asyncio
async def test_file_sqlite_uses_wal_and_busy_timeout(tmp_path, monkeypatch):
    database_path = tmp_path / "bay.db"
    settings = Settings(
        database=DatabaseConfig(
            url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
            echo=False,
        )
    )

    await db_session.close_db()
    monkeypatch.setattr(db_session, "get_settings", lambda: settings)

    engine = db_session._get_engine()
    try:
        async with engine.connect() as connection:
            journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
            synchronous = await connection.scalar(text("PRAGMA synchronous"))
            busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))

        assert journal_mode == "wal"
        assert synchronous == 2
        assert busy_timeout == 30_000
    finally:
        await db_session.close_db()
