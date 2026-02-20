"""Filesystem operation handlers (read / write / list / delete / upload / download)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from mcp.types import TextContent

from shipyard_neo_mcp import config as _config
from shipyard_neo_mcp.sandbox_cache import get_sandbox
from shipyard_neo_mcp.validators import (
    optional_str,
    require_str,
    truncate_text,
    validate_local_path,
    validate_relative_path,
    validate_sandbox_id,
)

logger = logging.getLogger("shipyard_neo_mcp")


async def handle_read_file(arguments: dict[str, Any]) -> list[TextContent]:
    """Read a file from the sandbox workspace."""
    sandbox_id = validate_sandbox_id(arguments)
    path = validate_relative_path(require_str(arguments, "path"))

    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        raw = await sandbox.filesystem.read_file(path)
    content = truncate_text(raw, limit=_config.MAX_TOOL_TEXT_CHARS)

    return [
        TextContent(
            type="text",
            text=f"**File: {path}**\n\n```\n{content}\n```",
        )
    ]


async def handle_write_file(arguments: dict[str, Any]) -> list[TextContent]:
    """Write content to a file in the sandbox workspace."""
    sandbox_id = validate_sandbox_id(arguments)
    path = validate_relative_path(require_str(arguments, "path"))
    content = require_str(arguments, "content")

    content_bytes = len(content.encode("utf-8"))
    if content_bytes > _config.MAX_WRITE_FILE_BYTES:
        raise ValueError(
            f"write_file content too large: {content_bytes} bytes "
            f"exceeds limit of {_config.MAX_WRITE_FILE_BYTES} bytes"
        )

    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        await sandbox.filesystem.write_file(path, content)

    return [
        TextContent(
            type="text",
            text=f"File `{path}` written successfully ({len(content)} bytes).",
        )
    ]


async def handle_list_files(arguments: dict[str, Any]) -> list[TextContent]:
    """List files and directories in the sandbox workspace."""
    sandbox_id = validate_sandbox_id(arguments)
    path = arguments.get("path", ".")
    if not isinstance(path, str):
        raise ValueError("field 'path' must be a string")
    path = validate_relative_path(path)

    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        entries = await sandbox.filesystem.list_dir(path)

    if not entries:
        return [
            TextContent(
                type="text",
                text=f"Directory `{path}` is empty.",
            )
        ]

    lines = [f"**Directory: {path}**\n"]
    for entry in entries:
        if entry.is_dir:
            lines.append(f"📁 {entry.name}/")
        else:
            size = f" ({entry.size} bytes)" if entry.size is not None else ""
            lines.append(f"📄 {entry.name}{size}")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_delete_file(arguments: dict[str, Any]) -> list[TextContent]:
    """Delete a file or directory from the sandbox workspace."""
    sandbox_id = validate_sandbox_id(arguments)
    path = validate_relative_path(require_str(arguments, "path"))

    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        await sandbox.filesystem.delete(path)

    return [
        TextContent(
            type="text",
            text=f"Deleted `{path}` successfully.",
        )
    ]


async def handle_upload_file(arguments: dict[str, Any]) -> list[TextContent]:
    """Upload a local file to a sandbox workspace."""
    sandbox_id = validate_sandbox_id(arguments)
    local_path_str = require_str(arguments, "local_path")
    local_path = validate_local_path(local_path_str)

    # Determine sandbox target path
    sandbox_path_raw = optional_str(arguments, "sandbox_path")
    if sandbox_path_raw:
        sandbox_path = validate_relative_path(sandbox_path_raw)
    else:
        sandbox_path = local_path.name

    # Validate local file exists and is readable
    if not local_path.exists():
        raise ValueError(f"local file not found: {local_path}")
    if not local_path.is_file():
        raise ValueError(f"local path is not a file: {local_path}")

    # Check file size
    file_size = local_path.stat().st_size
    if file_size > _config.MAX_TRANSFER_FILE_BYTES:
        raise ValueError(
            f"file too large: {file_size} bytes "
            f"exceeds limit of {_config.MAX_TRANSFER_FILE_BYTES} bytes"
        )

    # Read and upload
    content = local_path.read_bytes()
    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        await sandbox.filesystem.upload(sandbox_path, content)

    logger.info(
        "file_uploaded sandbox_id=%s local=%s sandbox=%s size=%d",
        sandbox_id,
        local_path,
        sandbox_path,
        file_size,
    )

    return [
        TextContent(
            type="text",
            text=(
                f"File uploaded successfully.\n\n"
                f"**Local:** `{local_path}`\n"
                f"**Sandbox:** `{sandbox_path}`\n"
                f"**Size:** {file_size} bytes"
            ),
        )
    ]


async def handle_download_file(arguments: dict[str, Any]) -> list[TextContent]:
    """Download a file from a sandbox workspace to the local filesystem."""
    sandbox_id = validate_sandbox_id(arguments)
    sandbox_path = validate_relative_path(require_str(arguments, "sandbox_path"))

    # Determine local destination
    local_path_str = optional_str(arguments, "local_path")
    if local_path_str:
        local_path = validate_local_path(local_path_str)
    else:
        # Use sandbox file name in current directory
        local_path = Path.cwd() / Path(sandbox_path).name

    # Download from sandbox
    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        content = await sandbox.filesystem.download(sandbox_path)

    # Check downloaded size
    if len(content) > _config.MAX_TRANSFER_FILE_BYTES:
        raise ValueError(
            f"downloaded file too large: {len(content)} bytes "
            f"exceeds limit of {_config.MAX_TRANSFER_FILE_BYTES} bytes"
        )

    # Create parent directories and write
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(content)

    logger.info(
        "file_downloaded sandbox_id=%s sandbox=%s local=%s size=%d",
        sandbox_id,
        sandbox_path,
        local_path,
        len(content),
    )

    return [
        TextContent(
            type="text",
            text=(
                f"File downloaded successfully.\n\n"
                f"**Sandbox:** `{sandbox_path}`\n"
                f"**Local:** `{local_path}`\n"
                f"**Size:** {len(content)} bytes"
            ),
        )
    ]
