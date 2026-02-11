"""Skill lifecycle handlers (candidates, evaluations, releases, payloads)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.types import TextContent

from shipyard_neo_mcp import config as _config
from shipyard_neo_mcp.sandbox_cache import get_client
from shipyard_neo_mcp.validators import (
    optional_str,
    read_bool,
    read_int,
    read_optional_number,
    read_release_stage,
    require_str,
    require_str_list,
    truncate_text,
)


async def handle_create_skill_payload(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Create a generic skill payload and return a stable payload_ref."""
    client = get_client()
    payload = arguments.get("payload")
    if not isinstance(payload, (dict, list)):
        raise ValueError("field 'payload' must be a JSON object or array")
    kind = optional_str(arguments, "kind") or "generic"
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        result = await client.skills.create_payload(payload=payload, kind=kind)
    return [
        TextContent(
            type="text",
            text=(
                f"Created skill payload {result.payload_ref}\n"
                f"kind: {result.kind}"
            ),
        )
    ]


async def handle_get_skill_payload(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Get one skill payload by payload_ref."""
    client = get_client()
    payload_ref = require_str(arguments, "payload_ref")
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        result = await client.skills.get_payload(payload_ref)
    payload_json = json.dumps(result.payload, ensure_ascii=False, default=str)
    return [
        TextContent(
            type="text",
            text=(
                f"payload_ref: {result.payload_ref}\n"
                f"kind: {result.kind}\n"
                f"payload:\n{truncate_text(payload_json, limit=_config.MAX_TOOL_TEXT_CHARS)}"
            ),
        )
    ]


async def handle_create_skill_candidate(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Create a reusable skill candidate from execution IDs."""
    client = get_client()
    skill_key = require_str(arguments, "skill_key")
    source_execution_ids = require_str_list(arguments, "source_execution_ids")
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        candidate = await client.skills.create_candidate(
            skill_key=skill_key,
            source_execution_ids=source_execution_ids,
            scenario_key=optional_str(arguments, "scenario_key"),
            payload_ref=optional_str(arguments, "payload_ref"),
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


async def handle_evaluate_skill_candidate(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Record evaluation result for a skill candidate."""
    client = get_client()
    candidate_id = require_str(arguments, "candidate_id")
    passed = arguments.get("passed")
    if not isinstance(passed, bool):
        raise ValueError("field 'passed' must be a boolean")
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        evaluation = await client.skills.evaluate_candidate(
            candidate_id,
            passed=passed,
            score=read_optional_number(arguments, "score"),
            benchmark_id=optional_str(arguments, "benchmark_id"),
            report=optional_str(arguments, "report"),
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


async def handle_promote_skill_candidate(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Promote a passing skill candidate to release."""
    client = get_client()
    candidate_id = require_str(arguments, "candidate_id")
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        release = await client.skills.promote_candidate(
            candidate_id,
            stage=read_release_stage(arguments, key="stage", default="canary"),
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


async def handle_list_skill_candidates(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """List skill candidates with optional filters."""
    client = get_client()
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        candidates = await client.skills.list_candidates(
            status=optional_str(arguments, "status"),
            skill_key=optional_str(arguments, "skill_key"),
            limit=read_int(arguments, "limit", 50, min_value=1, max_value=500),
            offset=read_int(arguments, "offset", 0, min_value=0),
        )
    if not candidates.items:
        return [TextContent(type="text", text="No skill candidates found.")]
    lines = [f"Total: {candidates.total}"]
    for item in candidates.items:
        lines.append(
            f"- {item.id} | {item.skill_key} | status={item.status.value} | pass={item.latest_pass}"
        )
    return [TextContent(type="text", text="\n".join(lines))]


async def handle_list_skill_releases(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """List skill releases with optional filters."""
    client = get_client()
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        releases = await client.skills.list_releases(
            skill_key=optional_str(arguments, "skill_key"),
            active_only=read_bool(arguments, "active_only", False),
            stage=read_release_stage(
                arguments, key="stage", required=False, default=None
            ),
            limit=read_int(arguments, "limit", 50, min_value=1, max_value=500),
            offset=read_int(arguments, "offset", 0, min_value=0),
        )
    if not releases.items:
        return [TextContent(type="text", text="No skill releases found.")]
    lines = [f"Total: {releases.total}"]
    for item in releases.items:
        lines.append(
            f"- {item.id} | {item.skill_key} v{item.version} | stage={item.stage.value} | active={item.is_active}"
        )
    return [TextContent(type="text", text="\n".join(lines))]


async def handle_rollback_skill_release(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Rollback an active release to a previous known-good version."""
    client = get_client()
    release_id = require_str(arguments, "release_id")
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        rollback_release = await client.skills.rollback_release(release_id)
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
