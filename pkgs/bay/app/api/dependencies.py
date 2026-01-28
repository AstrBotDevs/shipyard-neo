"""FastAPI dependencies for Bay API.

Provides dependency injection for:
- Database sessions
- Managers (Sandbox, Session, Workspace)
- Driver
- Authentication (TODO)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session_dependency
from app.drivers.base import Driver
from app.drivers.docker import DockerDriver
from app.managers.sandbox import SandboxManager


@lru_cache
def get_driver() -> Driver:
    """Get cached driver instance.
    
    Uses lru_cache to ensure single driver instance across requests.
    """
    settings = get_settings()
    if settings.driver.type == "docker":
        return DockerDriver()
    else:
        raise ValueError(f"Unsupported driver type: {settings.driver.type}")


async def get_sandbox_manager(
    session: Annotated[AsyncSession, Depends(get_session_dependency)],
) -> SandboxManager:
    """Get SandboxManager with injected dependencies."""
    driver = get_driver()
    return SandboxManager(driver=driver, db_session=session)


def get_current_owner(request: Request) -> str:
    """Get current owner from request.
    
    TODO: Implement proper JWT authentication.
    For now, returns a default owner for development.
    """
    # Check for Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        # TODO: Validate JWT and extract owner
        pass

    # Check for X-Owner header (development only)
    owner = request.headers.get("X-Owner")
    if owner:
        return owner

    # Default owner for development
    return "default"


# Type aliases for cleaner dependency injection
DriverDep = Annotated[Driver, Depends(get_driver)]
SessionDep = Annotated[AsyncSession, Depends(get_session_dependency)]
SandboxManagerDep = Annotated[SandboxManager, Depends(get_sandbox_manager)]
OwnerDep = Annotated[str, Depends(get_current_owner)]
