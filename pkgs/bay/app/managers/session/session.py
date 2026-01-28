"""SessionManager - manages session (container) lifecycle.

Key responsibility: ensure_running - idempotent session startup.

See: plans/bay-design.md section 3.2
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import ProfileConfig, get_settings
from app.drivers.base import ContainerStatus, Driver
from app.errors import NotFoundError, SessionNotReadyError
from app.models.session import Session, SessionStatus
from app.models.workspace import Workspace

if TYPE_CHECKING:
    from app.clients.runtime import RuntimeClient

logger = structlog.get_logger()


class SessionManager:
    """Manages session (container) lifecycle."""

    def __init__(
        self,
        driver: Driver,
        db_session: AsyncSession,
        runtime_client: "RuntimeClient | None" = None,
    ) -> None:
        self._driver = driver
        self._db = db_session
        self._runtime_client = runtime_client
        self._log = logger.bind(manager="session")
        self._settings = get_settings()

    async def create(
        self,
        sandbox_id: str,
        workspace: Workspace,
        profile: ProfileConfig,
    ) -> Session:
        """Create a new session record (does not start container).
        
        Args:
            sandbox_id: Sandbox ID
            workspace: Workspace to mount
            profile: Profile configuration
            
        Returns:
            Created session
        """
        session_id = f"sess-{uuid.uuid4().hex[:12]}"

        self._log.info(
            "session.create",
            session_id=session_id,
            sandbox_id=sandbox_id,
            profile_id=profile.id,
        )

        session = Session(
            id=session_id,
            sandbox_id=sandbox_id,
            runtime_type="ship",
            profile_id=profile.id,
            desired_state=SessionStatus.PENDING,
            observed_state=SessionStatus.PENDING,
            created_at=datetime.utcnow(),
            last_active_at=datetime.utcnow(),
        )

        self._db.add(session)
        await self._db.commit()
        await self._db.refresh(session)

        return session

    async def get(self, session_id: str) -> Session | None:
        """Get session by ID."""
        result = await self._db.exec(
            select(Session).where(Session.id == session_id)
        )
        return result.first()

    async def ensure_running(
        self,
        session: Session,
        workspace: Workspace,
        profile: ProfileConfig,
    ) -> Session:
        """Ensure session is running - create/start container if needed.
        
        This is the core idempotent startup logic.
        
        Args:
            session: Session to ensure is running
            workspace: Workspace to mount
            profile: Profile configuration
            
        Returns:
            Updated session with endpoint
            
        Raises:
            SessionNotReadyError: If session is starting but not ready yet
        """
        self._log.info(
            "session.ensure_running",
            session_id=session.id,
            observed_state=session.observed_state,
        )

        # Already running and ready
        if session.is_ready:
            return session

        # Currently starting - tell client to retry
        if session.observed_state == SessionStatus.STARTING:
            raise SessionNotReadyError(
                message="Session is starting",
                sandbox_id=session.sandbox_id,
                retry_after_ms=1000,
            )

        # Need to create container
        if session.container_id is None:
            session.desired_state = SessionStatus.RUNNING
            session.observed_state = SessionStatus.STARTING
            await self._db.commit()

            # Create container
            container_id = await self._driver.create(
                session=session,
                profile=profile,
                workspace=workspace,
            )

            session.container_id = container_id
            await self._db.commit()

        # Need to start container
        if session.observed_state != SessionStatus.RUNNING:
            try:
                endpoint = await self._driver.start(session.container_id)
                session.endpoint = endpoint
                session.observed_state = SessionStatus.RUNNING
                session.last_observed_at = datetime.utcnow()
                await self._db.commit()

                # TODO: Call Ship GET /meta for handshake validation
                # await self._validate_runtime(session, profile)

            except Exception as e:
                self._log.error(
                    "session.start_failed",
                    session_id=session.id,
                    error=str(e),
                )
                session.observed_state = SessionStatus.FAILED
                await self._db.commit()
                raise

        return session

    async def stop(self, session: Session) -> None:
        """Stop a session (reclaim compute).
        
        Args:
            session: Session to stop
        """
        self._log.info("session.stop", session_id=session.id)

        session.desired_state = SessionStatus.STOPPED
        session.observed_state = SessionStatus.STOPPING
        await self._db.commit()

        if session.container_id:
            await self._driver.stop(session.container_id)

        session.observed_state = SessionStatus.STOPPED
        session.endpoint = None
        session.last_observed_at = datetime.utcnow()
        await self._db.commit()

    async def destroy(self, session: Session) -> None:
        """Destroy a session completely.
        
        Args:
            session: Session to destroy
        """
        self._log.info("session.destroy", session_id=session.id)

        if session.container_id:
            await self._driver.destroy(session.container_id)

        await self._db.delete(session)
        await self._db.commit()

    async def refresh_status(self, session: Session) -> Session:
        """Refresh session status from driver.
        
        Args:
            session: Session to refresh
            
        Returns:
            Updated session
        """
        if not session.container_id:
            return session

        info = await self._driver.status(session.container_id)

        # Map container status to session status
        if info.status == ContainerStatus.RUNNING:
            session.observed_state = SessionStatus.RUNNING
            session.endpoint = info.endpoint
        elif info.status == ContainerStatus.CREATED:
            session.observed_state = SessionStatus.PENDING
        elif info.status == ContainerStatus.EXITED:
            session.observed_state = SessionStatus.STOPPED
        elif info.status == ContainerStatus.NOT_FOUND:
            session.observed_state = SessionStatus.STOPPED
            session.container_id = None

        session.last_observed_at = datetime.utcnow()
        await self._db.commit()

        return session

    async def touch(self, session_id: str) -> None:
        """Update last_active_at timestamp."""
        result = await self._db.exec(
            select(Session).where(Session.id == session_id)
        )
        session = result.first()

        if session:
            session.last_active_at = datetime.utcnow()
            await self._db.commit()
