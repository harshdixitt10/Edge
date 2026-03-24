"""
Internal Event Bus — asyncio.Queue wrapper decoupling ingest from forwarding.

Events are published by adapters and consumed by the store writer + cloud connector.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.models import DataEvent

logger = logging.getLogger(__name__)


class EventBus:
    """Thread-safe asyncio Queue-based event bus for the data pipeline."""

    def __init__(self, maxsize: int = 10_000):
        self._queue: asyncio.Queue[DataEvent] = asyncio.Queue(maxsize=maxsize)
        self._subscribers: list[asyncio.Queue] = []

    async def publish(self, event: DataEvent) -> None:
        """Publish an event onto the bus."""
        await self._queue.put(event)
        # Also push to any additional subscribers
        for sub_queue in self._subscribers:
            try:
                sub_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # subscriber too slow, skip

    async def consume(self) -> DataEvent:
        """Consume the next event from the bus (blocks until available)."""
        return await self._queue.get()

    def task_done(self) -> None:
        """Mark a consumed event as processed."""
        self._queue.task_done()

    def subscribe(self, maxsize: int = 1000) -> asyncio.Queue:
        """Create a subscriber queue for live event feeds (e.g., dashboard)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        if q in self._subscribers:
            self._subscribers.remove(q)

    @property
    def pending(self) -> int:
        """Number of events waiting in the main queue."""
        return self._queue.qsize()
