"""
Workspace management utilities for handling session-based file system operations.

This module provides utilities for managing per-session workspaces with user isolation,
ensuring that each session has its own isolated file system environment and Linux user
while maintaining security by preventing access outside the designated workspace directories.
"""

import pwd
from pathlib import Path
from fastapi import HTTPException
from .components.user_manager import get_or_create_session_user


def get_user_workspace_dir(username: str) -> Path:
    """
    获取用户的workspace目录路径

    Args:
        username: Linux用户名

    Returns:
        Path: 用户的workspace目录路径
    """
    try:
        user_info = pwd.getpwnam(username)
        user_home = Path(user_info.pw_dir)
        workspace_dir = user_home / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir
    except KeyError:
        raise HTTPException(status_code=404, detail=f"User {username} not found")


async def get_session_workspace(session_id: str) -> Path:
    """
    获取session对应用户的工作目录

    Args:
        session_id: 会话ID

    Returns:
        Path: 用户的工作目录路径
    """
    username = await get_or_create_session_user(session_id)
    return get_user_workspace_dir(username)


async def resolve_path(session_id: str, path: str) -> Path:
    """
    安全的路径解析

    Args:
        session_id: 会话ID
        path: 要解析的路径

    Returns:
        Path: 解析后的绝对路径

    Raises:
        HTTPException: 当路径在工作目录外时抛出403错误
    """
    workspace_dir = (await get_session_workspace(session_id)).resolve()
    candidate = Path(path)

    if not candidate.is_absolute():
        candidate = workspace_dir / candidate

    candidate = candidate.resolve()
    try:
        candidate.relative_to(workspace_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within workspace {workspace_dir}",
        )

    return candidate


def resolve_user_path(username: str, path: str) -> Path:
    """
    为指定用户安全地解析路径

    Args:
        username: Linux用户名
        path: 要解析的路径

    Returns:
        Path: 解析后的绝对路径

    Raises:
        HTTPException: 当路径在工作目录外时抛出403错误
    """
    workspace_dir = get_user_workspace_dir(username).resolve()
    candidate = Path(path)

    if not candidate.is_absolute():
        candidate = workspace_dir / candidate

    candidate = candidate.resolve()
    try:
        candidate.relative_to(workspace_dir)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: path must be within workspace {workspace_dir}",
        )

    return candidate
