"""Configuration management for the Shipyard Neo MCP server."""

from __future__ import annotations

import os
from typing import Any


def _read_positive_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    if parsed <= 0:
        return default
    return parsed


MAX_TOOL_TEXT_CHARS = _read_positive_int_env("SHIPYARD_MAX_TOOL_TEXT_CHARS", 12000)
MAX_SANDBOX_CACHE_SIZE = _read_positive_int_env("SHIPYARD_SANDBOX_CACHE_SIZE", 256)
MAX_WRITE_FILE_BYTES = _read_positive_int_env(
    "SHIPYARD_MAX_WRITE_FILE_BYTES", 5 * 1024 * 1024
)
MAX_TRANSFER_FILE_BYTES = _read_positive_int_env(
    "SHIPYARD_MAX_TRANSFER_FILE_BYTES", 50 * 1024 * 1024
)
SDK_CALL_TIMEOUT = _read_positive_int_env("SHIPYARD_SDK_CALL_TIMEOUT", 600)


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

    default_profile = os.environ.get("SHIPYARD_DEFAULT_PROFILE", "python-default")

    # Allow ttl=0 for infinite TTL.
    default_ttl_raw = os.environ.get("SHIPYARD_DEFAULT_TTL", "3600")
    try:
        default_ttl = int(default_ttl_raw)
    except ValueError:
        default_ttl = 3600
    if default_ttl < 0:
        default_ttl = 3600

    return {
        "endpoint_url": endpoint,
        "access_token": token,
        "default_profile": default_profile,
        "default_ttl": default_ttl,
    }
