"""Process-wide adapter pool for capability routing.

Goal: allow adapter instances (and their internal `/meta` caches) to be reused
across requests. CapabilityRouter itself is typically request-scoped, so we
need a process-scoped pool to make caching effective.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Generic, TypeVar

if TYPE_CHECKING:
    from app.adapters.base import BaseAdapter


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _PoolEntry(Generic[T]):
    value: T
    expires_at: float  # monotonic time


class AdapterPool(Generic[T]):
    """A small LRU pool with TTL eviction."""

    def __init__(
        self,
        *,
        max_size: int,
        ttl_seconds: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._now = now

        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _PoolEntry[T]] = OrderedDict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get_or_create(self, key: str, factory: Callable[[], T]) -> T:
        now = self._now()

        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                if existing.expires_at > now:
                    # LRU: refresh recency order (but keep absolute TTL).
                    self._entries.move_to_end(key)
                    return existing.value
                # Expired.
                self._entries.pop(key, None)

        # Create outside the lock; construction may be non-trivial.
        value = factory()
        entry = _PoolEntry(value=value, expires_at=now + self._ttl_seconds)

        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            self._evict_locked(now)

        return value

    def _evict_locked(self, now: float) -> None:
        # Drop expired entries first.
        expired_keys = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._entries.pop(key, None)

        # Then enforce size cap (LRU eviction).
        while len(self._entries) > self._max_size:
            self._entries.popitem(last=False)


# Default process-wide pool used by CapabilityRouter.
default_adapter_pool: AdapterPool["BaseAdapter"] = AdapterPool(
    max_size=128,
    ttl_seconds=300.0,
)
