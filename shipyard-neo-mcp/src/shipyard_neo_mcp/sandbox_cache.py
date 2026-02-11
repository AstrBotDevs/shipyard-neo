"""Sandbox instance cache with LRU eviction."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from shipyard_neo_mcp import config as _config

logger = logging.getLogger("shipyard_neo_mcp")

# Global client instance (managed by lifespan)
_client: Any = None
_sandboxes: OrderedDict[str, Any] = OrderedDict()
_sandboxes_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Return the sandbox cache lock, creating it lazily if needed."""
    global _sandboxes_lock
    if _sandboxes_lock is None:
        _sandboxes_lock = asyncio.Lock()
    return _sandboxes_lock


def cache_sandbox(sandbox: Any) -> None:
    """Add or refresh a sandbox in the LRU cache, evicting if needed."""
    sandbox_id = getattr(sandbox, "id", None)
    if not isinstance(sandbox_id, str) or not sandbox_id:
        return
    if sandbox_id in _sandboxes:
        _sandboxes.move_to_end(sandbox_id)
    _sandboxes[sandbox_id] = sandbox
    while len(_sandboxes) > _config.MAX_SANDBOX_CACHE_SIZE:
        evicted_id, _ = _sandboxes.popitem(last=False)
        logger.debug(
            "cache_evict sandbox_id=%s cache_size=%d", evicted_id, len(_sandboxes)
        )


def set_client(client: Any) -> None:
    """Set the global BayClient instance."""
    global _client
    _client = client


def get_client() -> Any:
    """Return the global BayClient instance."""
    return _client


def clear() -> None:
    """Clear the sandbox cache."""
    _sandboxes.clear()


async def get_sandbox(sandbox_id: str) -> Any:
    """Get or fetch a sandbox by ID with cache lock protection."""
    if _client is None:
        raise RuntimeError("BayClient not initialized")

    lock = _get_lock()
    async with lock:
        if sandbox_id in _sandboxes:
            _sandboxes.move_to_end(sandbox_id)
            return _sandboxes[sandbox_id]

    # Fetch from server (outside lock to avoid holding it during I/O)
    async with asyncio.timeout(_config.SDK_CALL_TIMEOUT):
        sandbox = await _client.get_sandbox(sandbox_id)

    async with lock:
        cache_sandbox(sandbox)
    return sandbox
