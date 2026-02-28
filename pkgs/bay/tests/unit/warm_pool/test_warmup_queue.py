"""Unit tests for WarmupQueue."""

from __future__ import annotations

import asyncio

import pytest

from app.config import WarmPoolConfig
from app.services.warm_pool.queue import WarmupQueue


def _make_config(**overrides) -> WarmPoolConfig:
    defaults = {
        "warmup_queue_workers": 1,
        "warmup_queue_max_size": 4,
        "warmup_queue_drop_policy": "drop_newest",
        "warmup_queue_drop_alert_threshold": 50,
        "interval_seconds": 30,
    }
    defaults.update(overrides)
    return WarmPoolConfig(**defaults)


class TestWarmupQueueEnqueue:
    """Tests for enqueue behavior."""

    def test_enqueue_success(self):
        """Normal enqueue should succeed."""
        config = _make_config()
        queue = WarmupQueue(config=config)

        result = queue.enqueue(sandbox_id="sb-1", owner="user-1")

        assert result is True
        assert queue.depth == 1
        assert queue.stats.enqueue_total == 1

    def test_enqueue_dedup(self):
        """Duplicate sandbox_id should be deduped."""
        config = _make_config()
        queue = WarmupQueue(config=config)

        queue.enqueue(sandbox_id="sb-1", owner="user-1")
        result = queue.enqueue(sandbox_id="sb-1", owner="user-1")

        assert result is False
        assert queue.depth == 1
        assert queue.stats.dedup_total == 1

    def test_enqueue_drop_newest_when_full(self):
        """When queue is full, drop_newest policy drops the new item."""
        config = _make_config(warmup_queue_max_size=2, warmup_queue_drop_policy="drop_newest")
        queue = WarmupQueue(config=config)

        queue.enqueue(sandbox_id="sb-1", owner="u")
        queue.enqueue(sandbox_id="sb-2", owner="u")
        result = queue.enqueue(sandbox_id="sb-3", owner="u")

        assert result is False
        assert queue.depth == 2
        assert queue.stats.drop_total == 1

    def test_enqueue_drop_oldest_when_full(self):
        """When queue is full, drop_oldest policy evicts oldest and enqueues new."""
        config = _make_config(warmup_queue_max_size=2, warmup_queue_drop_policy="drop_oldest")
        queue = WarmupQueue(config=config)

        queue.enqueue(sandbox_id="sb-1", owner="u")
        queue.enqueue(sandbox_id="sb-2", owner="u")
        result = queue.enqueue(sandbox_id="sb-3", owner="u")

        assert result is True
        assert queue.depth == 2
        assert queue.stats.drop_total == 1
        assert queue.stats.enqueue_total == 3

    def test_multiple_different_sandbox_ids(self):
        """Different sandbox IDs should all be enqueued."""
        config = _make_config()
        queue = WarmupQueue(config=config)

        for i in range(4):
            result = queue.enqueue(sandbox_id=f"sb-{i}", owner="u")
            assert result is True

        assert queue.depth == 4
        assert queue.stats.enqueue_total == 4
        assert queue.stats.dedup_total == 0


class TestWarmupQueueLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Queue should start and stop cleanly."""
        config = _make_config()
        queue = WarmupQueue(config=config)

        assert not queue.is_running

        await queue.start()
        assert queue.is_running

        await queue.stop()
        assert not queue.is_running

    @pytest.mark.asyncio
    async def test_double_start(self):
        """Double start should be idempotent."""
        config = _make_config()
        queue = WarmupQueue(config=config)

        await queue.start()
        await queue.start()  # Should not raise

        await queue.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        """Stop without start should be safe."""
        config = _make_config()
        queue = WarmupQueue(config=config)

        await queue.stop()  # Should not raise


class TestWarmupQueueStats:
    """Tests for observable statistics."""

    def test_initial_stats(self):
        config = _make_config()
        queue = WarmupQueue(config=config)

        stats = queue.stats
        assert stats.enqueue_total == 0
        assert stats.dedup_total == 0
        assert stats.drop_total == 0
        assert stats.consumed_total == 0
        assert stats.success_total == 0
        assert stats.failure_total == 0
        assert stats.active_workers == 0

    def test_stats_after_enqueue(self):
        config = _make_config()
        queue = WarmupQueue(config=config)

        queue.enqueue(sandbox_id="sb-1", owner="u")
        queue.enqueue(sandbox_id="sb-1", owner="u")  # dedup
        queue.enqueue(sandbox_id="sb-2", owner="u")

        assert queue.stats.enqueue_total == 2
        assert queue.stats.dedup_total == 1
