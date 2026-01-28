"""Sandboxes API endpoints.

See: plans/bay-api.md section 6.1
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field

from app.errors import NotFoundError

router = APIRouter()


# Request/Response Models


class CreateSandboxRequest(BaseModel):
    """Request to create a sandbox."""

    profile: str = "python-default"
    workspace_id: str | None = None
    ttl: int | None = None  # seconds, null/0 = no expiry


class SandboxResponse(BaseModel):
    """Sandbox response model."""

    id: str
    status: str
    profile: str
    workspace_id: str
    capabilities: list[str]
    created_at: datetime
    expires_at: datetime | None
    idle_expires_at: datetime | None


class SandboxListResponse(BaseModel):
    """Sandbox list response."""

    items: list[SandboxResponse]
    next_cursor: str | None = None


# Endpoints


@router.post("", response_model=SandboxResponse, status_code=201)
async def create_sandbox(
    request: CreateSandboxRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> SandboxResponse:
    """Create a new sandbox.
    
    - Lazy session creation: status may be 'idle' initially
    - ttl=null or ttl=0 means no expiry
    """
    # TODO: Implement with SandboxManager
    # For now, return a placeholder
    raise NotImplementedError("Sandbox creation not yet implemented")


@router.get("", response_model=SandboxListResponse)
async def list_sandboxes(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    status: str | None = Query(None),
) -> SandboxListResponse:
    """List sandboxes for the current user."""
    # TODO: Implement with SandboxManager
    return SandboxListResponse(items=[], next_cursor=None)


@router.get("/{sandbox_id}", response_model=SandboxResponse)
async def get_sandbox(sandbox_id: str) -> SandboxResponse:
    """Get sandbox details."""
    # TODO: Implement with SandboxManager
    raise NotFoundError(f"Sandbox not found: {sandbox_id}")


@router.post("/{sandbox_id}/keepalive", status_code=200)
async def keepalive(sandbox_id: str) -> dict[str, str]:
    """Keep sandbox alive - extends idle timeout only, not TTL.
    
    Does not implicitly start compute if no session exists.
    """
    # TODO: Implement with SandboxManager
    raise NotFoundError(f"Sandbox not found: {sandbox_id}")


@router.post("/{sandbox_id}/stop", status_code=200)
async def stop_sandbox(sandbox_id: str) -> dict[str, str]:
    """Stop sandbox - reclaims compute, keeps workspace.
    
    Idempotent: repeated calls maintain final state consistency.
    """
    # TODO: Implement with SandboxManager
    raise NotFoundError(f"Sandbox not found: {sandbox_id}")


@router.delete("/{sandbox_id}", status_code=204)
async def delete_sandbox(sandbox_id: str) -> None:
    """Delete sandbox permanently.
    
    - Destroys all running sessions
    - Cascade deletes managed workspace
    - Does NOT cascade delete external workspace
    """
    # TODO: Implement with SandboxManager
    raise NotFoundError(f"Sandbox not found: {sandbox_id}")
