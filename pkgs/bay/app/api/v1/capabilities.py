"""Capabilities API endpoints (through Sandbox).

These endpoints route capability requests to the Ship runtime.
See: plans/bay-api.md section 6.2
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.dependencies import OwnerDep, SandboxManagerDep
from app.router.capability import CapabilityRouter

router = APIRouter()


# Request/Response Models


class PythonExecRequest(BaseModel):
    """Request to execute Python code."""

    code: str
    timeout: int = Field(default=30, ge=1, le=300)


class PythonExecResponse(BaseModel):
    """Python execution response."""

    success: bool
    output: str
    error: str | None = None
    data: dict[str, Any] | None = None


class ShellExecRequest(BaseModel):
    """Request to execute shell command."""

    command: str
    timeout: int = Field(default=30, ge=1, le=300)
    cwd: str | None = None  # Relative to /workspace


class ShellExecResponse(BaseModel):
    """Shell execution response."""

    success: bool
    output: str
    error: str | None = None
    exit_code: int | None = None


class FileReadRequest(BaseModel):
    """Request to read a file."""

    path: str  # Relative to /workspace


class FileReadResponse(BaseModel):
    """File read response."""

    content: str


class FileWriteRequest(BaseModel):
    """Request to write a file."""

    path: str  # Relative to /workspace
    content: str


class FileListRequest(BaseModel):
    """Request to list directory."""

    path: str = "."  # Relative to /workspace


class FileListResponse(BaseModel):
    """File list response."""

    entries: list[dict[str, Any]]


class FileDeleteRequest(BaseModel):
    """Request to delete file/directory."""

    path: str  # Relative to /workspace


# Endpoints


@router.post("/{sandbox_id}/python/exec", response_model=PythonExecResponse)
async def exec_python(
    sandbox_id: str,
    request: PythonExecRequest,
    sandbox_mgr: SandboxManagerDep,
    owner: OwnerDep,
) -> PythonExecResponse:
    """Execute Python code in sandbox.
    
    This will:
    1. Ensure sandbox has a running session (auto-start if needed)
    2. Route execution to Ship runtime
    3. Return results
    """
    sandbox = await sandbox_mgr.get(sandbox_id, owner)
    capability_router = CapabilityRouter(sandbox_mgr)

    result = await capability_router.exec_python(
        sandbox=sandbox,
        code=request.code,
        timeout=request.timeout,
    )

    return PythonExecResponse(
        success=result.success,
        output=result.output,
        error=result.error,
        data=result.data,
    )


@router.post("/{sandbox_id}/shell/exec", response_model=ShellExecResponse)
async def exec_shell(
    sandbox_id: str,
    request: ShellExecRequest,
    sandbox_mgr: SandboxManagerDep,
    owner: OwnerDep,
) -> ShellExecResponse:
    """Execute shell command in sandbox."""
    sandbox = await sandbox_mgr.get(sandbox_id, owner)
    capability_router = CapabilityRouter(sandbox_mgr)

    result = await capability_router.exec_shell(
        sandbox=sandbox,
        command=request.command,
        timeout=request.timeout,
        cwd=request.cwd,
    )

    return ShellExecResponse(
        success=result.success,
        output=result.output,
        error=result.error,
        exit_code=result.exit_code,
    )


@router.post("/{sandbox_id}/files/read", response_model=FileReadResponse)
async def read_file(
    sandbox_id: str,
    request: FileReadRequest,
    sandbox_mgr: SandboxManagerDep,
    owner: OwnerDep,
) -> FileReadResponse:
    """Read file from sandbox."""
    sandbox = await sandbox_mgr.get(sandbox_id, owner)
    capability_router = CapabilityRouter(sandbox_mgr)

    content = await capability_router.read_file(sandbox=sandbox, path=request.path)

    return FileReadResponse(content=content)


@router.post("/{sandbox_id}/files/write", status_code=200)
async def write_file(
    sandbox_id: str,
    request: FileWriteRequest,
    sandbox_mgr: SandboxManagerDep,
    owner: OwnerDep,
) -> dict[str, str]:
    """Write file to sandbox."""
    sandbox = await sandbox_mgr.get(sandbox_id, owner)
    capability_router = CapabilityRouter(sandbox_mgr)

    await capability_router.write_file(
        sandbox=sandbox,
        path=request.path,
        content=request.content,
    )

    return {"status": "ok"}


@router.post("/{sandbox_id}/files/list", response_model=FileListResponse)
async def list_files(
    sandbox_id: str,
    request: FileListRequest,
    sandbox_mgr: SandboxManagerDep,
    owner: OwnerDep,
) -> FileListResponse:
    """List directory contents in sandbox."""
    sandbox = await sandbox_mgr.get(sandbox_id, owner)
    capability_router = CapabilityRouter(sandbox_mgr)

    entries = await capability_router.list_files(sandbox=sandbox, path=request.path)

    return FileListResponse(entries=entries)


@router.post("/{sandbox_id}/files/delete", status_code=200)
async def delete_file(
    sandbox_id: str,
    request: FileDeleteRequest,
    sandbox_mgr: SandboxManagerDep,
    owner: OwnerDep,
) -> dict[str, str]:
    """Delete file or directory from sandbox."""
    sandbox = await sandbox_mgr.get(sandbox_id, owner)
    capability_router = CapabilityRouter(sandbox_mgr)

    await capability_router.delete_file(sandbox=sandbox, path=request.path)

    return {"status": "ok"}
