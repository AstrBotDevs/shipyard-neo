"""Code execution handlers (Python / Shell)."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.types import TextContent

from shipyard_neo_mcp import config as _config
from shipyard_neo_mcp.sandbox_cache import get_sandbox
from shipyard_neo_mcp.validators import (
    optional_str,
    read_bool,
    read_int,
    require_str,
    truncate_text,
    validate_sandbox_id,
)


async def handle_execute_python(arguments: dict[str, Any]) -> list[TextContent]:
    """Execute Python code in a sandbox."""
    sandbox_id = validate_sandbox_id(arguments)
    code = require_str(arguments, "code")
    timeout = read_int(arguments, "timeout", 30, min_value=1, max_value=300)
    include_code = read_bool(arguments, "include_code", False)
    description = optional_str(arguments, "description")
    tags = optional_str(arguments, "tags")

    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        result = await sandbox.python.exec(
            code,
            timeout=timeout,
            include_code=include_code,
            description=description,
            tags=tags,
        )

    if result.success:
        output = truncate_text(result.output or "(no output)", limit=_config.MAX_TOOL_TEXT_CHARS)
        suffix = ""
        if result.execution_id:
            suffix += f"\n\nexecution_id: {result.execution_id}"
        if result.execution_time_ms is not None:
            suffix += f"\nexecution_time_ms: {result.execution_time_ms}"
        if include_code and result.code:
            suffix += f"\n\ncode:\n{truncate_text(result.code, limit=_config.MAX_TOOL_TEXT_CHARS)}"
        return [
            TextContent(
                type="text",
                text=f"**Execution successful**\n\n```\n{output}\n```{suffix}",
            )
        ]
    else:
        error = truncate_text(result.error or "Unknown error", limit=_config.MAX_TOOL_TEXT_CHARS)
        suffix = ""
        if result.execution_id:
            suffix += f"\n\nexecution_id: {result.execution_id}"
        return [
            TextContent(
                type="text",
                text=f"**Execution failed**\n\n```\n{error}\n```{suffix}",
            )
        ]


async def handle_execute_shell(arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a shell command in a sandbox."""
    sandbox_id = validate_sandbox_id(arguments)
    command = require_str(arguments, "command")
    cwd = optional_str(arguments, "cwd")
    timeout = read_int(arguments, "timeout", 30, min_value=1, max_value=300)
    include_code = read_bool(arguments, "include_code", False)
    description = optional_str(arguments, "description")
    tags = optional_str(arguments, "tags")

    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        result = await sandbox.shell.exec(
            command,
            cwd=cwd,
            timeout=timeout,
            include_code=include_code,
            description=description,
            tags=tags,
        )

    output = truncate_text(result.output or "(no output)", limit=_config.MAX_TOOL_TEXT_CHARS)
    status = "successful" if result.success else "failed"
    exit_code = result.exit_code if result.exit_code is not None else "N/A"
    suffix = ""
    if result.execution_id:
        suffix += f"\n\nexecution_id: {result.execution_id}"
    if result.execution_time_ms is not None:
        suffix += f"\nexecution_time_ms: {result.execution_time_ms}"
    if include_code and result.command:
        suffix += f"\n\ncommand:\n{truncate_text(result.command, limit=_config.MAX_TOOL_TEXT_CHARS)}"

    return [
        TextContent(
            type="text",
            text=f"**Command {status}** (exit code: {exit_code})\n\n```\n{output}\n```{suffix}",
        )
    ]
