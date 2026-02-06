"""Shipyard Neo MCP Server implementation.

This server exposes Shipyard Neo SDK functionality through MCP protocol,
allowing AI agents to create sandboxes and execute code securely.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
)

from shipyard_neo import BayClient, BayError


# Global client instance (managed by lifespan)
_client: BayClient | None = None
_sandboxes: dict[str, Any] = {}  # Cache sandbox objects by ID


def get_config() -> dict[str, Any]:
    """Get configuration from environment variables."""
    endpoint = os.environ.get("SHIPYARD_ENDPOINT_URL") or os.environ.get("BAY_ENDPOINT")
    token = os.environ.get("SHIPYARD_ACCESS_TOKEN") or os.environ.get("BAY_TOKEN")

    if not endpoint:
        raise ValueError(
            "SHIPYARD_ENDPOINT_URL environment variable is required. "
            "Set it in your MCP configuration."
        )
    if not token:
        raise ValueError(
            "SHIPYARD_ACCESS_TOKEN environment variable is required. "
            "Set it in your MCP configuration."
        )

    return {
        "endpoint_url": endpoint,
        "access_token": token,
        "default_profile": os.environ.get("SHIPYARD_DEFAULT_PROFILE", "python-default"),
        "default_ttl": int(os.environ.get("SHIPYARD_DEFAULT_TTL", "3600")),
    }


@asynccontextmanager
async def lifespan(server: Server):
    """Manage the BayClient lifecycle."""
    global _client
    config = get_config()

    _client = BayClient(
        endpoint_url=config["endpoint_url"],
        access_token=config["access_token"],
    )
    await _client.__aenter__()

    try:
        yield
    finally:
        await _client.__aexit__(None, None, None)
        _client = None
        _sandboxes.clear()


# Create MCP server
server = Server("shipyard-neo-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="create_sandbox",
            description="Create a new sandbox environment for executing code. Returns the sandbox ID which must be used for subsequent operations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "string",
                        "description": "Runtime profile (e.g., 'python-default'). Defaults to 'python-default'.",
                    },
                    "ttl": {
                        "type": "integer",
                        "description": "Time-to-live in seconds. Defaults to 3600 (1 hour). Use 0 for no expiration.",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="delete_sandbox",
            description="Delete a sandbox and clean up all resources.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {
                        "type": "string",
                        "description": "The sandbox ID to delete.",
                    },
                },
                "required": ["sandbox_id"],
            },
        ),
        Tool(
            name="execute_python",
            description="Execute Python code in a sandbox. Variables persist across calls within the same sandbox session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {
                        "type": "string",
                        "description": "The sandbox ID to execute in.",
                    },
                    "code": {
                        "type": "string",
                        "description": "Python code to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds. Defaults to 30.",
                    },
                },
                "required": ["sandbox_id", "code"],
            },
        ),
        Tool(
            name="execute_shell",
            description="Execute a shell command in a sandbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {
                        "type": "string",
                        "description": "The sandbox ID to execute in.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (relative to /workspace). Defaults to workspace root.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds. Defaults to 30.",
                    },
                },
                "required": ["sandbox_id", "command"],
            },
        ),
        Tool(
            name="read_file",
            description="Read a file from the sandbox workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {
                        "type": "string",
                        "description": "The sandbox ID.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path relative to /workspace.",
                    },
                },
                "required": ["sandbox_id", "path"],
            },
        ),
        Tool(
            name="write_file",
            description="Write content to a file in the sandbox workspace. Creates parent directories automatically.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {
                        "type": "string",
                        "description": "The sandbox ID.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path relative to /workspace.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write.",
                    },
                },
                "required": ["sandbox_id", "path", "content"],
            },
        ),
        Tool(
            name="list_files",
            description="List files and directories in the sandbox workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {
                        "type": "string",
                        "description": "The sandbox ID.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to /workspace. Defaults to '.' (workspace root).",
                    },
                },
                "required": ["sandbox_id"],
            },
        ),
        Tool(
            name="delete_file",
            description="Delete a file or directory from the sandbox workspace.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {
                        "type": "string",
                        "description": "The sandbox ID.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to delete, relative to /workspace.",
                    },
                },
                "required": ["sandbox_id", "path"],
            },
        ),
    ]


async def get_sandbox(sandbox_id: str):
    """Get or fetch a sandbox by ID."""
    global _client, _sandboxes

    if _client is None:
        raise RuntimeError("BayClient not initialized")

    if sandbox_id in _sandboxes:
        return _sandboxes[sandbox_id]

    # Fetch from server
    sandbox = await _client.get_sandbox(sandbox_id)
    _sandboxes[sandbox_id] = sandbox
    return sandbox


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    global _client, _sandboxes

    if _client is None:
        return [TextContent(type="text", text="Error: BayClient not initialized")]

    try:
        if name == "create_sandbox":
            config = get_config()
            profile = arguments.get("profile", config["default_profile"])
            ttl = arguments.get("ttl", config["default_ttl"])

            sandbox = await _client.create_sandbox(profile=profile, ttl=ttl)
            _sandboxes[sandbox.id] = sandbox

            return [
                TextContent(
                    type="text",
                    text=f"Sandbox created successfully.\n\n"
                    f"**Sandbox ID:** `{sandbox.id}`\n"
                    f"**Profile:** {sandbox.profile}\n"
                    f"**Status:** {sandbox.status.value}\n"
                    f"**Capabilities:** {', '.join(sandbox.capabilities)}\n"
                    f"**TTL:** {ttl} seconds\n\n"
                    f"Use this sandbox_id for subsequent operations.",
                )
            ]

        elif name == "delete_sandbox":
            sandbox_id = arguments["sandbox_id"]
            sandbox = await get_sandbox(sandbox_id)
            await sandbox.delete()
            _sandboxes.pop(sandbox_id, None)

            return [
                TextContent(
                    type="text",
                    text=f"Sandbox `{sandbox_id}` deleted successfully.",
                )
            ]

        elif name == "execute_python":
            sandbox_id = arguments["sandbox_id"]
            code = arguments["code"]
            timeout = arguments.get("timeout", 30)

            sandbox = await get_sandbox(sandbox_id)
            result = await sandbox.python.exec(code, timeout=timeout)

            if result.success:
                output = result.output or "(no output)"
                return [
                    TextContent(
                        type="text",
                        text=f"**Execution successful**\n\n```\n{output}\n```",
                    )
                ]
            else:
                error = result.error or "Unknown error"
                return [
                    TextContent(
                        type="text",
                        text=f"**Execution failed**\n\n```\n{error}\n```",
                    )
                ]

        elif name == "execute_shell":
            sandbox_id = arguments["sandbox_id"]
            command = arguments["command"]
            cwd = arguments.get("cwd")
            timeout = arguments.get("timeout", 30)

            sandbox = await get_sandbox(sandbox_id)
            result = await sandbox.shell.exec(command, cwd=cwd, timeout=timeout)

            output = result.output or "(no output)"
            status = "successful" if result.success else "failed"
            exit_code = result.exit_code if result.exit_code is not None else "N/A"

            return [
                TextContent(
                    type="text",
                    text=f"**Command {status}** (exit code: {exit_code})\n\n```\n{output}\n```",
                )
            ]

        elif name == "read_file":
            sandbox_id = arguments["sandbox_id"]
            path = arguments["path"]

            sandbox = await get_sandbox(sandbox_id)
            content = await sandbox.filesystem.read_file(path)

            return [
                TextContent(
                    type="text",
                    text=f"**File: {path}**\n\n```\n{content}\n```",
                )
            ]

        elif name == "write_file":
            sandbox_id = arguments["sandbox_id"]
            path = arguments["path"]
            content = arguments["content"]

            sandbox = await get_sandbox(sandbox_id)
            await sandbox.filesystem.write_file(path, content)

            return [
                TextContent(
                    type="text",
                    text=f"File `{path}` written successfully ({len(content)} bytes).",
                )
            ]

        elif name == "list_files":
            sandbox_id = arguments["sandbox_id"]
            path = arguments.get("path", ".")

            sandbox = await get_sandbox(sandbox_id)
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

        elif name == "delete_file":
            sandbox_id = arguments["sandbox_id"]
            path = arguments["path"]

            sandbox = await get_sandbox(sandbox_id)
            await sandbox.filesystem.delete(path)

            return [
                TextContent(
                    type="text",
                    text=f"Deleted `{path}` successfully.",
                )
            ]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except BayError as e:
        return [TextContent(type="text", text=f"**API Error:** {e.message}")]
    except Exception as e:
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
