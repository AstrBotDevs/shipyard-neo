"""Shipyard Neo MCP Server implementation.

This server exposes Shipyard Neo SDK functionality through MCP protocol,
allowing AI agents to create sandboxes and execute code securely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from shipyard_neo import BayError

from shipyard_neo_mcp import config as _config_mod
from shipyard_neo_mcp import sandbox_cache as _cache_mod
from shipyard_neo_mcp.config import get_config  # noqa: F401
from shipyard_neo_mcp.validators import (  # noqa: F401
    validate_sandbox_id as _validate_sandbox_id,
    validate_relative_path as _validate_relative_path,
    validate_local_path as _validate_local_path,
    truncate_text as _truncate_text,
    require_str as _require_str,
    optional_str as _optional_str,
    read_bool as _read_bool,
    read_int as _read_int,
    read_optional_number as _read_optional_number,
    read_exec_type as _read_exec_type,
    read_release_stage as _read_release_stage,
    require_str_list as _require_str_list,
)
from shipyard_neo_mcp.tool_defs import get_tool_definitions
from shipyard_neo_mcp.handlers import TOOL_HANDLERS


logger = logging.getLogger("shipyard_neo_mcp")

# ── Backward-compatibility layer ──
# Tests do things like:
#   mcp_server._client = FakeClient()
#   mcp_server._sandboxes["sbx-1"] = FakeSandbox()
#   monkeypatch.setattr(mcp_server, "_MAX_SANDBOX_CACHE_SIZE", 2)
#
# The actual state now lives in sub-modules (config, sandbox_cache).
# We use a custom module class to transparently proxy reads and writes
# to the correct sub-module, so all existing tests pass unchanged.

# Map of attribute name -> (target_module, target_attr_name)
_PROXY_MAP: dict[str, tuple[Any, str]] = {
    "_client": (_cache_mod, "_client"),
    "_sandboxes": (_cache_mod, "_sandboxes"),
    "_sandboxes_lock": (_cache_mod, "_sandboxes_lock"),
    "_MAX_TOOL_TEXT_CHARS": (_config_mod, "MAX_TOOL_TEXT_CHARS"),
    "_MAX_SANDBOX_CACHE_SIZE": (_config_mod, "MAX_SANDBOX_CACHE_SIZE"),
    "_MAX_WRITE_FILE_BYTES": (_config_mod, "MAX_WRITE_FILE_BYTES"),
    "_MAX_TRANSFER_FILE_BYTES": (_config_mod, "MAX_TRANSFER_FILE_BYTES"),
    "_SDK_CALL_TIMEOUT": (_config_mod, "SDK_CALL_TIMEOUT"),
}


_module = sys.modules[__name__]
_original_class = type(_module)


class _BackcompatModule(_original_class):
    """Module subclass that proxies attribute access to sub-modules."""

    def __getattr__(self, name: str) -> Any:
        entry = _PROXY_MAP.get(name)
        if entry is not None:
            mod, attr = entry
            return getattr(mod, attr)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        entry = _PROXY_MAP.get(name)
        if entry is not None:
            mod, attr = entry
            setattr(mod, attr, value)
            return
        super().__setattr__(name, value)


_module.__class__ = _BackcompatModule


# Re-export functions that tests call directly on `mcp_server`
def _cache_sandbox(sandbox: Any) -> None:
    """Backward-compat wrapper around sandbox_cache.cache_sandbox."""
    _cache_mod.cache_sandbox(sandbox)


def _get_lock() -> asyncio.Lock:
    """Backward-compat wrapper around sandbox_cache._get_lock."""
    return _cache_mod._get_lock()


get_sandbox = _cache_mod.get_sandbox


def _format_bay_error(error: BayError) -> str:
    suffix = ""
    if error.details:
        serialized = json.dumps(error.details, ensure_ascii=False, default=str)
        suffix = f"\n\ndetails: {_truncate_text(serialized, limit=1000)}"
    return f"**API Error:** [{error.code}] {error.message}{suffix}"


@asynccontextmanager
async def lifespan(server: Server):
    """Manage the BayClient lifecycle."""
    from shipyard_neo import BayClient

    config = get_config()
    client = BayClient(
        endpoint_url=config["endpoint_url"],
        access_token=config["access_token"],
    )
    await client.__aenter__()
    _cache_mod._client = client

    try:
        yield
    finally:
        await client.__aexit__(None, None, None)
        _cache_mod._client = None
        _cache_mod._sandboxes.clear()


# Create MCP server
server = Server("shipyard-neo-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return get_tool_definitions()


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls by dispatching to the appropriate handler."""
    if _cache_mod._client is None:
        return [TextContent(type="text", text="Error: BayClient not initialized")]

    try:
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        return await handler(arguments)

    except ValueError as e:
        return [TextContent(type="text", text=f"**Validation Error:** {e!s}")]
    except TimeoutError:
        logger.warning(
            "tool_timeout tool=%s timeout=%ds", name, _config_mod.SDK_CALL_TIMEOUT
        )
        return [
            TextContent(
                type="text",
                text=f"**Timeout Error:** SDK call timed out after {_config_mod.SDK_CALL_TIMEOUT}s",
            )
        ]
    except BayError as e:
        logger.warning("bay_error tool=%s code=%s message=%s", name, e.code, e.message)
        return [TextContent(type="text", text=_format_bay_error(e))]
    except Exception as e:
        logger.exception("unexpected_error tool=%s", name)
        return [TextContent(type="text", text=f"**Error:** {e!s}")]


async def run_server():
    """Run the MCP server."""
    async with lifespan(server):
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


def main():
    """Main entry point."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Server error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
