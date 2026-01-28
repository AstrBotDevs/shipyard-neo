"""Driver base class - infrastructure abstraction.

Driver is responsible ONLY for container lifecycle management.
It does NOT handle:
- Authentication
- Retry/circuit-breaker
- Audit logging
- Rate limiting
- Quota management

See: plans/bay-design.md section 3.1
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import ProfileConfig
    from app.models.session import Session
    from app.models.workspace import Workspace


class ContainerStatus(str, Enum):
    """Container status from driver's perspective."""

    CREATED = "created"
    RUNNING = "running"
    EXITED = "exited"
    REMOVING = "removing"
    NOT_FOUND = "not_found"


@dataclass
class ContainerInfo:
    """Container information from driver."""

    container_id: str
    status: ContainerStatus
    endpoint: str | None = None  # Ship REST API endpoint
    exit_code: int | None = None


class Driver(ABC):
    """Abstract driver interface for container lifecycle management.
    
    All resources created by driver MUST be labeled with:
    - owner
    - sandbox_id
    - session_id
    - workspace_id
    - profile_id
    """

    @abstractmethod
    async def create(
        self,
        session: "Session",
        profile: "ProfileConfig",
        workspace: "Workspace",
        *,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Create a container without starting it.
        
        Args:
            session: Session model
            profile: Profile configuration
            workspace: Workspace to mount
            labels: Additional labels for the container
            
        Returns:
            Container ID
        """
        ...

    @abstractmethod
    async def start(self, container_id: str) -> str:
        """Start a container and return its endpoint.
        
        Args:
            container_id: Container ID from create()
            
        Returns:
            Ship REST API endpoint URL
        """
        ...

    @abstractmethod
    async def stop(self, container_id: str) -> None:
        """Stop a running container.
        
        Args:
            container_id: Container ID
        """
        ...

    @abstractmethod
    async def destroy(self, container_id: str) -> None:
        """Destroy (remove) a container.
        
        Args:
            container_id: Container ID
        """
        ...

    @abstractmethod
    async def status(self, container_id: str) -> ContainerInfo:
        """Get container status.
        
        Args:
            container_id: Container ID
            
        Returns:
            Container information
        """
        ...

    @abstractmethod
    async def logs(self, container_id: str, tail: int = 100) -> str:
        """Get container logs.
        
        Args:
            container_id: Container ID
            tail: Number of lines to return
            
        Returns:
            Log content
        """
        ...

    # Volume management (for Workspace)

    @abstractmethod
    async def create_volume(self, name: str, labels: dict[str, str] | None = None) -> str:
        """Create a volume for workspace.
        
        Args:
            name: Volume name
            labels: Volume labels
            
        Returns:
            Volume name (for reference)
        """
        ...

    @abstractmethod
    async def delete_volume(self, name: str) -> None:
        """Delete a volume.
        
        Args:
            name: Volume name
        """
        ...

    @abstractmethod
    async def volume_exists(self, name: str) -> bool:
        """Check if volume exists.
        
        Args:
            name: Volume name
            
        Returns:
            True if volume exists
        """
        ...
