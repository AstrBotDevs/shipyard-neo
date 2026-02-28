"""Warm pool service for pre-warming sandbox instances.

This module provides:
- WarmupQueue: In-process bounded queue with fixed workers for warmup throttling
- WarmPoolScheduler: Periodic pool maintenance (replenish + rotate)
- Lifecycle management for FastAPI lifespan integration

Usage:
    from app.services.warm_pool import WarmupQueue, WarmPoolScheduler

    queue = WarmupQueue(config=settings.warm_pool)
    await queue.start()
"""

from app.services.warm_pool.queue import WarmupQueue
from app.services.warm_pool.scheduler import WarmPoolScheduler

__all__ = [
    "WarmupQueue",
    "WarmPoolScheduler",
]
