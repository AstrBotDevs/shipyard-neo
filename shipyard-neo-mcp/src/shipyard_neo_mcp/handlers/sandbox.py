"""Sandbox lifecycle handlers (create / delete)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.types import TextContent

from shipyard_neo_mcp import config as _config
from shipyard_neo_mcp.sandbox_cache import (
    cache_sandbox,
    get_client,
    get_sandbox,
    _get_lock,
    _sandboxes,
)
from shipyard_neo_mcp.validators import read_int, validate_sandbox_id

logger = logging.getLogger("shipyard_neo_mcp")


async def handle_create_sandbox(arguments: dict[str, Any]) -> list[TextContent]:
    """Create a new sandbox environment."""
    client = get_client()
    config = _config.get_config()
    profile = arguments.get("profile", config["default_profile"])
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("field 'profile' must be a non-empty string")
    ttl = read_int(arguments, "ttl", config["default_ttl"], min_value=0)

    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        sandbox = await client.create_sandbox(profile=profile, ttl=ttl)
    async with _get_lock():
        cache_sandbox(sandbox)

    logger.info(
        "sandbox_created sandbox_id=%s profile=%s ttl=%d",
        sandbox.id,
        sandbox.profile,
        ttl,
    )

    # Build containers info if available
    containers_text = ""
    if getattr(sandbox, "containers", None):
        lines = ["**Containers:**"]
        for c in sandbox.containers:
            ver = getattr(c, "version", None) or "unknown"
            healthy = getattr(c, "healthy", None)
            health_str = (
                "✅" if healthy is True
                else "❌" if healthy is False
                else "?"
            )
            rt = getattr(c, "runtime_type", "unknown")
            name = getattr(c, "name", "unknown")
            caps = ", ".join(getattr(c, "capabilities", []))
            lines.append(
                f"  - {name} ({rt}) v{ver} {health_str} [{caps}]"
            )
        containers_text = "\n".join(lines) + "\n"

    return [
        TextContent(
            type="text",
            text=f"Sandbox created successfully.\n\n"
            f"**Sandbox ID:** `{sandbox.id}`\n"
            f"**Profile:** {sandbox.profile}\n"
            f"**Status:** {sandbox.status.value}\n"
            f"**Capabilities:** {', '.join(sandbox.capabilities)}\n"
            f"**TTL:** {ttl} seconds\n"
            f"{containers_text}\n"
            f"Use this sandbox_id for subsequent operations.",
        )
    ]


async def handle_delete_sandbox(arguments: dict[str, Any]) -> list[TextContent]:
    """Delete a sandbox and clean up resources."""
    sandbox_id = validate_sandbox_id(arguments)
    sandbox = await get_sandbox(sandbox_id)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        await sandbox.delete()
    async with _get_lock():
        _sandboxes.pop(sandbox_id, None)

    logger.info("sandbox_deleted sandbox_id=%s", sandbox_id)

    return [
        TextContent(
            type="text",
            text=f"Sandbox `{sandbox_id}` deleted successfully.",
        )
    ]
