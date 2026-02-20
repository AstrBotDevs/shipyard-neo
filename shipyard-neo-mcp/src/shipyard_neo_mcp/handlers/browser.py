"""Browser automation handlers (single command and batch)."""

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
    require_str_list,
    truncate_text,
    validate_sandbox_id,
)


async def handle_execute_browser(arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a single browser automation command in a sandbox."""
    sandbox_id = validate_sandbox_id(arguments)
    cmd = require_str(arguments, "cmd")
    timeout = read_int(arguments, "timeout", 30, min_value=1, max_value=300)
    description = optional_str(arguments, "description")
    tags = optional_str(arguments, "tags")
    learn = read_bool(arguments, "learn", False)
    include_trace = read_bool(arguments, "include_trace", False)

    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        result = await sandbox.browser.exec(
            cmd,
            timeout=timeout,
            description=description,
            tags=tags,
            learn=learn,
            include_trace=include_trace,
        )

    output = truncate_text(
        result.output or "(no output)", limit=_config.MAX_TOOL_TEXT_CHARS
    )
    status = "successful" if result.success else "failed"
    exit_code = result.exit_code if result.exit_code is not None else "N/A"
    suffix = ""
    execution_id = getattr(result, "execution_id", None)
    execution_time_ms = getattr(result, "execution_time_ms", None)
    trace_ref = getattr(result, "trace_ref", None)
    if execution_id:
        suffix += f"\n\nexecution_id: {execution_id}"
    if execution_time_ms is not None:
        suffix += f"\nexecution_time_ms: {execution_time_ms}"
    if trace_ref:
        suffix += f"\ntrace_ref: {trace_ref}"
    error_suffix = ""
    if not result.success and result.error:
        error_suffix = f"\n\nstderr:\n{truncate_text(result.error, limit=_config.MAX_TOOL_TEXT_CHARS)}"

    return [
        TextContent(
            type="text",
            text=(
                f"**Browser command {status}** (exit code: {exit_code})\n\n"
                f"```\n{output}\n```{suffix}{error_suffix}"
            ),
        )
    ]


async def handle_execute_browser_batch(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Execute a sequence of browser automation commands in one request."""
    sandbox_id = validate_sandbox_id(arguments)
    commands = require_str_list(arguments, "commands")
    timeout = read_int(arguments, "timeout", 60, min_value=1, max_value=600)
    stop_on_error = read_bool(arguments, "stop_on_error", True)
    description = optional_str(arguments, "description")
    tags = optional_str(arguments, "tags")
    learn = read_bool(arguments, "learn", False)
    include_trace = read_bool(arguments, "include_trace", False)

    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        result = await sandbox.browser.exec_batch(
            commands,
            timeout=timeout,
            stop_on_error=stop_on_error,
            description=description,
            tags=tags,
            learn=learn,
            include_trace=include_trace,
        )

    lines = [
        f"**Batch execution {'completed' if result.success else 'failed'}** "
        f"({result.completed_steps}/{result.total_steps} steps, {result.duration_ms}ms)\n"
    ]
    execution_id = getattr(result, "execution_id", None)
    execution_time_ms = getattr(result, "execution_time_ms", None)
    trace_ref = getattr(result, "trace_ref", None)
    if execution_id:
        lines.append(f"execution_id: {execution_id}")
    if execution_time_ms is not None:
        lines.append(f"execution_time_ms: {execution_time_ms}")
    if trace_ref:
        lines.append(f"trace_ref: {trace_ref}")
    for step in result.results:
        status_icon = "✅" if step.exit_code == 0 else "❌"
        lines.append(
            f"{status_icon} Step {step.step_index}: `{step.cmd}` "
            f"(exit={step.exit_code}, {step.duration_ms}ms)"
        )
        if step.stdout.strip():
            lines.append(f"   stdout: {truncate_text(step.stdout.strip(), limit=500)}")
        if step.stderr.strip():
            lines.append(f"   stderr: {truncate_text(step.stderr.strip(), limit=500)}")

    return [TextContent(type="text", text="\n".join(lines))]
