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
                    "include_code": {
                        "type": "boolean",
                        "description": "Include executed code and execution metadata in response.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description for execution history annotation.",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Optional comma-separated tags for execution history.",
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
                    "include_code": {
                        "type": "boolean",
                        "description": "Include executed command and execution metadata in response.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description for execution history annotation.",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Optional comma-separated tags for execution history.",
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
        Tool(
            name="get_execution_history",
            description="Get execution history for a sandbox with optional filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {"type": "string", "description": "The sandbox ID."},
                    "exec_type": {
                        "type": "string",
                        "description": "Optional execution type filter: python or shell.",
                    },
                    "success_only": {
                        "type": "boolean",
                        "description": "Return only successful executions.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of entries. Defaults to 50.",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Optional comma-separated tags filter.",
                    },
                    "has_notes": {
                        "type": "boolean",
                        "description": "Return only entries that have notes.",
                    },
                    "has_description": {
                        "type": "boolean",
                        "description": "Return only entries that have description.",
                    },
                },
                "required": ["sandbox_id"],
            },
        ),
        Tool(
            name="get_execution",
            description="Get one execution record by execution ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {"type": "string", "description": "The sandbox ID."},
                    "execution_id": {"type": "string", "description": "Execution record ID."},
                },
                "required": ["sandbox_id", "execution_id"],
            },
        ),
        Tool(
            name="get_last_execution",
            description="Get the latest execution record in a sandbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {"type": "string", "description": "The sandbox ID."},
                    "exec_type": {
                        "type": "string",
                        "description": "Optional execution type filter: python or shell.",
                    },
                },
                "required": ["sandbox_id"],
            },
        ),
        Tool(
            name="annotate_execution",
            description="Add or update description/tags/notes for one execution record.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sandbox_id": {"type": "string", "description": "The sandbox ID."},
                    "execution_id": {"type": "string", "description": "Execution record ID."},
                    "description": {"type": "string", "description": "Description text."},
                    "tags": {"type": "string", "description": "Comma-separated tags."},
                    "notes": {"type": "string", "description": "Agent notes."},
                },
                "required": ["sandbox_id", "execution_id"],
            },
        ),
        Tool(
            name="create_skill_candidate",
            description="Create a reusable skill candidate from execution IDs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "skill_key": {"type": "string", "description": "Skill identifier."},
                    "source_execution_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Execution IDs used as source evidence.",
                    },
                    "scenario_key": {"type": "string", "description": "Optional scenario key."},
                    "payload_ref": {"type": "string", "description": "Optional payload reference."},
                },
                "required": ["skill_key", "source_execution_ids"],
            },
        ),
        Tool(
            name="evaluate_skill_candidate",
            description="Record evaluation result for a skill candidate.",
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string", "description": "Skill candidate ID."},
                    "passed": {"type": "boolean", "description": "Whether evaluation passed."},
                    "score": {"type": "number", "description": "Optional evaluation score."},
                    "benchmark_id": {"type": "string", "description": "Optional benchmark ID."},
                    "report": {"type": "string", "description": "Optional evaluation report."},
                },
                "required": ["candidate_id", "passed"],
            },
        ),
        Tool(
            name="promote_skill_candidate",
            description="Promote a passing skill candidate to release.",
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string", "description": "Skill candidate ID."},
                    "stage": {
                        "type": "string",
                        "description": "Release stage: canary or stable. Defaults to canary.",
                    },
                },
                "required": ["candidate_id"],
            },
        ),
        Tool(
            name="list_skill_candidates",
            description="List skill candidates with optional filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Optional status filter."},
                    "skill_key": {"type": "string", "description": "Optional skill key filter."},
                    "limit": {"type": "integer", "description": "Max items. Defaults to 50."},
                    "offset": {"type": "integer", "description": "Offset. Defaults to 0."},
                },
                "required": [],
            },
        ),
        Tool(
            name="list_skill_releases",
            description="List skill releases with optional filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "skill_key": {"type": "string", "description": "Optional skill key filter."},
                    "active_only": {"type": "boolean", "description": "Only active releases."},
                    "stage": {"type": "string", "description": "Optional stage filter."},
                    "limit": {"type": "integer", "description": "Max items. Defaults to 50."},
                    "offset": {"type": "integer", "description": "Offset. Defaults to 0."},
                },
                "required": [],
            },
        ),
        Tool(
            name="rollback_skill_release",
            description="Rollback an active release to a previous known-good version.",
            inputSchema={
                "type": "object",
                "properties": {
                    "release_id": {"type": "string", "description": "Release ID to rollback from."},
                },
                "required": ["release_id"],
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
            include_code = arguments.get("include_code", False)
            description = arguments.get("description")
            tags = arguments.get("tags")

            sandbox = await get_sandbox(sandbox_id)
            result = await sandbox.python.exec(
                code,
                timeout=timeout,
                include_code=include_code,
                description=description,
                tags=tags,
            )

            if result.success:
                output = result.output or "(no output)"
                suffix = ""
                if result.execution_id:
                    suffix += f"\n\nexecution_id: {result.execution_id}"
                if result.execution_time_ms is not None:
                    suffix += f"\nexecution_time_ms: {result.execution_time_ms}"
                if include_code and result.code:
                    suffix += f"\n\ncode:\n{result.code}"
                return [
                    TextContent(
                        type="text",
                        text=f"**Execution successful**\n\n```\n{output}\n```{suffix}",
                    )
                ]
            else:
                error = result.error or "Unknown error"
                suffix = ""
                if result.execution_id:
                    suffix += f"\n\nexecution_id: {result.execution_id}"
                return [
                    TextContent(
                        type="text",
                        text=f"**Execution failed**\n\n```\n{error}\n```{suffix}",
                    )
                ]

        elif name == "execute_shell":
            sandbox_id = arguments["sandbox_id"]
            command = arguments["command"]
            cwd = arguments.get("cwd")
            timeout = arguments.get("timeout", 30)
            include_code = arguments.get("include_code", False)
            description = arguments.get("description")
            tags = arguments.get("tags")

            sandbox = await get_sandbox(sandbox_id)
            result = await sandbox.shell.exec(
                command,
                cwd=cwd,
                timeout=timeout,
                include_code=include_code,
                description=description,
                tags=tags,
            )

            output = result.output or "(no output)"
            status = "successful" if result.success else "failed"
            exit_code = result.exit_code if result.exit_code is not None else "N/A"
            suffix = ""
            if result.execution_id:
                suffix += f"\n\nexecution_id: {result.execution_id}"
            if result.execution_time_ms is not None:
                suffix += f"\nexecution_time_ms: {result.execution_time_ms}"
            if include_code and result.command:
                suffix += f"\n\ncommand:\n{result.command}"

            return [
                TextContent(
                    type="text",
                    text=f"**Command {status}** (exit code: {exit_code})\n\n```\n{output}\n```{suffix}",
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

        elif name == "get_execution_history":
            sandbox_id = arguments["sandbox_id"]
            sandbox = await get_sandbox(sandbox_id)

            history = await sandbox.get_execution_history(
                exec_type=arguments.get("exec_type"),
                success_only=arguments.get("success_only", False),
                limit=arguments.get("limit", 50),
                tags=arguments.get("tags"),
                has_notes=arguments.get("has_notes", False),
                has_description=arguments.get("has_description", False),
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

        elif name == "get_execution":
            sandbox_id = arguments["sandbox_id"]
            execution_id = arguments["execution_id"]
            sandbox = await get_sandbox(sandbox_id)
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
                        f"code:\n{entry.code}\n\n"
                        f"output:\n{entry.output or ''}\n\n"
                        f"error:\n{entry.error or ''}"
                    ),
                )
            ]

        elif name == "get_last_execution":
            sandbox_id = arguments["sandbox_id"]
            sandbox = await get_sandbox(sandbox_id)
            entry = await sandbox.get_last_execution(exec_type=arguments.get("exec_type"))
            return [
                TextContent(
                    type="text",
                    text=(
                        f"execution_id: {entry.id}\n"
                        f"type: {entry.exec_type}\n"
                        f"success: {entry.success}\n"
                        f"time_ms: {entry.execution_time_ms}\n"
                        f"code:\n{entry.code}"
                    ),
                )
            ]

        elif name == "annotate_execution":
            sandbox_id = arguments["sandbox_id"]
            execution_id = arguments["execution_id"]
            sandbox = await get_sandbox(sandbox_id)
            entry = await sandbox.annotate_execution(
                execution_id,
                description=arguments.get("description"),
                tags=arguments.get("tags"),
                notes=arguments.get("notes"),
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

        elif name == "create_skill_candidate":
            skill_key = arguments["skill_key"]
            source_execution_ids = arguments.get("source_execution_ids", [])
            candidate = await _client.skills.create_candidate(
                skill_key=skill_key,
                source_execution_ids=source_execution_ids,
                scenario_key=arguments.get("scenario_key"),
                payload_ref=arguments.get("payload_ref"),
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Created skill candidate {candidate.id}\n"
                        f"skill_key: {candidate.skill_key}\n"
                        f"status: {candidate.status.value}\n"
                        f"source_execution_ids: {', '.join(candidate.source_execution_ids)}"
                    ),
                )
            ]

        elif name == "evaluate_skill_candidate":
            candidate_id = arguments["candidate_id"]
            evaluation = await _client.skills.evaluate_candidate(
                candidate_id,
                passed=arguments["passed"],
                score=arguments.get("score"),
                benchmark_id=arguments.get("benchmark_id"),
                report=arguments.get("report"),
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Evaluation recorded: {evaluation.id}\n"
                        f"candidate_id: {evaluation.candidate_id}\n"
                        f"passed: {evaluation.passed}\n"
                        f"score: {evaluation.score}"
                    ),
                )
            ]

        elif name == "promote_skill_candidate":
            candidate_id = arguments["candidate_id"]
            release = await _client.skills.promote_candidate(
                candidate_id,
                stage=arguments.get("stage", "canary"),
            )
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Candidate promoted: {candidate_id}\n"
                        f"release_id: {release.id}\n"
                        f"skill_key: {release.skill_key}\n"
                        f"version: {release.version}\n"
                        f"stage: {release.stage.value}\n"
                        f"active: {release.is_active}"
                    ),
                )
            ]

        elif name == "list_skill_candidates":
            candidates = await _client.skills.list_candidates(
                status=arguments.get("status"),
                skill_key=arguments.get("skill_key"),
                limit=arguments.get("limit", 50),
                offset=arguments.get("offset", 0),
            )
            if not candidates.items:
                return [TextContent(type="text", text="No skill candidates found.")]
            lines = [f"Total: {candidates.total}"]
            for item in candidates.items:
                lines.append(
                    f"- {item.id} | {item.skill_key} | status={item.status.value} | pass={item.latest_pass}"
                )
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "list_skill_releases":
            releases = await _client.skills.list_releases(
                skill_key=arguments.get("skill_key"),
                active_only=arguments.get("active_only", False),
                stage=arguments.get("stage"),
                limit=arguments.get("limit", 50),
                offset=arguments.get("offset", 0),
            )
            if not releases.items:
                return [TextContent(type="text", text="No skill releases found.")]
            lines = [f"Total: {releases.total}"]
            for item in releases.items:
                lines.append(
                    f"- {item.id} | {item.skill_key} v{item.version} | stage={item.stage.value} | active={item.is_active}"
                )
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "rollback_skill_release":
            release_id = arguments["release_id"]
            rollback_release = await _client.skills.rollback_release(release_id)
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Rollback completed.\n"
                        f"new_release_id: {rollback_release.id}\n"
                        f"skill_key: {rollback_release.skill_key}\n"
                        f"version: {rollback_release.version}\n"
                        f"rollback_of: {rollback_release.rollback_of}"
                    ),
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
