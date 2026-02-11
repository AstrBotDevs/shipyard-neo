"""Execution history handlers."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.types import TextContent

from shipyard_neo_mcp import config as _config
from shipyard_neo_mcp.sandbox_cache import get_sandbox
from shipyard_neo_mcp.validators import (
    optional_str,
    read_bool,
    read_exec_type,
    read_int,
    require_str,
    truncate_text,
    validate_sandbox_id,
)


async def handle_get_execution_history(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Get execution history for a sandbox with optional filters."""
    sandbox_id = validate_sandbox_id(arguments)
    sandbox = await get_sandbox(sandbox_id)

    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        history = await sandbox.get_execution_history(
            exec_type=read_exec_type(arguments, "exec_type"),
            success_only=read_bool(arguments, "success_only", False),
            limit=read_int(arguments, "limit", 50, min_value=1, max_value=500),
            tags=optional_str(arguments, "tags"),
            has_notes=read_bool(arguments, "has_notes", False),
            has_description=read_bool(arguments, "has_description", False),
        )

    if not history.entries:
        return [TextContent(type="text", text="No execution history found.")]

    lines = [f"Total: {history.total}"]
    for entry in history.entries:
        lines.append(
            f"- {entry.id} | {entry.exec_type} | success={entry.success} | {entry.execution_time_ms}ms"
        )
        if entry.description:
            lines.append(f"  description: {entry.description}")
        if entry.tags:
            lines.append(f"  tags: {entry.tags}")
    return [TextContent(type="text", text="\n".join(lines))]


async def handle_get_execution(arguments: dict[str, Any]) -> list[TextContent]:
    """Get one execution record by execution ID."""
    sandbox_id = validate_sandbox_id(arguments)
    execution_id = require_str(arguments, "execution_id")
    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        entry = await sandbox.get_execution(execution_id)
    return [
        TextContent(
            type="text",
            text=(
                f"execution_id: {entry.id}\n"
                f"type: {entry.exec_type}\n"
                f"success: {entry.success}\n"
                f"time_ms: {entry.execution_time_ms}\n"
                f"tags: {entry.tags or ''}\n"
                f"description: {entry.description or ''}\n"
                f"notes: {entry.notes or ''}\n\n"
                f"code:\n{truncate_text(entry.code, limit=_config.MAX_TOOL_TEXT_CHARS)}\n\n"
                f"output:\n{truncate_text(entry.output, limit=_config.MAX_TOOL_TEXT_CHARS)}\n\n"
                f"error:\n{truncate_text(entry.error, limit=_config.MAX_TOOL_TEXT_CHARS)}"
            ),
        )
    ]


async def handle_get_last_execution(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Get the latest execution record in a sandbox."""
    sandbox_id = validate_sandbox_id(arguments)
    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        entry = await sandbox.get_last_execution(
            exec_type=read_exec_type(arguments, "exec_type")
        )
    return [
        TextContent(
            type="text",
            text=(
                f"execution_id: {entry.id}\n"
                f"type: {entry.exec_type}\n"
                f"success: {entry.success}\n"
                f"time_ms: {entry.execution_time_ms}\n"
                f"code:\n{truncate_text(entry.code, limit=_config.MAX_TOOL_TEXT_CHARS)}"
            ),
        )
    ]


async def handle_annotate_execution(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Add or update description/tags/notes for one execution record."""
    sandbox_id = validate_sandbox_id(arguments)
    execution_id = require_str(arguments, "execution_id")
    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        entry = await sandbox.annotate_execution(
            execution_id,
            description=optional_str(arguments, "description"),
            tags=optional_str(arguments, "tags"),
            notes=optional_str(arguments, "notes"),
        )
    return [
        TextContent(
            type="text",
            text=(
                f"Updated execution {entry.id}\n"
                f"description: {entry.description or ''}\n"
                f"tags: {entry.tags or ''}\n"
                f"notes: {entry.notes or ''}"
            ),
        )
    ]
