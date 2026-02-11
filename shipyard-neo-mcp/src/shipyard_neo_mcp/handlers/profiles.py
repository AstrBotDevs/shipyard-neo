"""Profile listing handler."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.types import TextContent

from shipyard_neo_mcp import config as _config
from shipyard_neo_mcp.sandbox_cache import get_client


async def handle_list_profiles(arguments: dict[str, Any]) -> list[TextContent]:
    """List available sandbox profiles."""
    client = get_client()
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        profiles = await client.list_profiles(detail=True)

    if not profiles.items:
        return [TextContent(type="text", text="No profiles available.")]

    lines = [f"**Available Profiles** ({len(profiles.items)})\n"]
    for p in profiles.items:
        caps = ", ".join(p.capabilities) if p.capabilities else "none"
        desc = f" — {p.description}" if p.description else ""
        lines.append(
            f"- **{p.id}**{desc}: capabilities=[{caps}], "
            f"idle_timeout={p.idle_timeout}s"
        )
        if p.containers:
            for c in p.containers:
                c_caps = ", ".join(c.capabilities) if c.capabilities else "none"
                lines.append(f"    └ {c.name} ({c.runtime_type}): [{c_caps}]")

    return [TextContent(type="text", text="\n".join(lines))]
