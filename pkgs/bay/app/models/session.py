"""Session data model.

Session represents a running container instance.
- 1 Session = 1 Container
- Can be idle-recycled and recreated transparently
- Not exposed to external API (only sandbox_id is exposed)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.sandbox import Sandbox


class SessionStatus(str, Enum):
    """Session lifecycle status."""

    PENDING = "pending"  # Waiting to be created
    STARTING = "starting"  # Container starting up
    RUNNING = "running"  # Running and healthy
    STOPPING = "stopping"  # Stopping in progress
    STOPPED = "stopped"  # Stopped
    FAILED = "failed"  # Start failed


class Session(SQLModel, table=True):
    """Session - running container instance."""

    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    sandbox_id: str = Field(foreign_key="sandboxes.id", index=True)

    # Runtime info
    runtime_type: str = Field(default="ship")  # ship | browser | gpu (future)
    profile_id: str = Field(default="python-default")

    # Container info
    container_id: str | None = Field(default=None)
    endpoint: str | None = Field(default=None)  # Ship REST API endpoint

    # State management (desired vs observed)
    desired_state: SessionStatus = Field(default=SessionStatus.PENDING)
    observed_state: SessionStatus = Field(default=SessionStatus.PENDING)
    last_observed_at: datetime | None = Field(default=None)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    sandbox: "Sandbox" = Relationship(back_populates="sessions")

    @property
    def is_ready(self) -> bool:
        """Check if session is ready to accept requests."""
        return self.observed_state == SessionStatus.RUNNING and self.endpoint is not None

    @property
    def is_running(self) -> bool:
        """Check if session is running (may not be ready yet)."""
        return self.observed_state in (SessionStatus.STARTING, SessionStatus.RUNNING)
