"""Profiles API endpoints.

GET /v1/profiles - List available profiles
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter()


class ResourceSpecResponse(BaseModel):
    """Resource specification response."""

    cpus: float
    memory: str


class ProfileResponse(BaseModel):
    """Profile response model."""

    id: str
    image: str
    resources: ResourceSpecResponse
    capabilities: list[str]
    idle_timeout: int


class ProfileListResponse(BaseModel):
    """Profile list response."""

    items: list[ProfileResponse]


@router.get("", response_model=ProfileListResponse)
async def list_profiles() -> ProfileListResponse:
    """List available profiles."""
    settings = get_settings()
    items = [
        ProfileResponse(
            id=p.id,
            image=p.image,
            resources=ResourceSpecResponse(
                cpus=p.resources.cpus,
                memory=p.resources.memory,
            ),
            capabilities=p.capabilities,
            idle_timeout=p.idle_timeout,
        )
        for p in settings.profiles
    ]
    return ProfileListResponse(items=items)
