"""
Watchdog — supervises all asyncio tasks and auto-restarts crashed ones.

Ensures the application never crashes permanently. All critical coroutines
(adapter, cloud connector, backfill, web server) are registered and monitored.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Coroutine

logger = logging.getLogger(__name__)


class Watchdog:
    """Task supervisor that restarts failed async tasks."""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._factories: dict[str, Callable[[], Coroutine]] = {}
        self._restart_delays: dict[str, float] = {}
        self._max_restart_delay: float = 30.0
        self._running = False

    def register(self, name: str, coro_factory: Callable[[], Coroutine],
                 restart_delay: float = 2.0) -> None:
        """Register a coroutine factory for supervision.
        
        Args:
            name: Unique name for the task
            coro_factory: Callable that returns a new coroutine instance
            restart_delay: Initial delay before restart (exponential backoff)
        """
        self._factories[name] = coro_factory
        self._restart_delays[name] = restart_delay
        logger.info(f"Watchdog: registered task '{name}'")

    def unregister(self, name: str) -> None:
        """Unregister and cancel a task."""
        if name in self._tasks:
            self._tasks[name].cancel()
            del self._tasks[name]
        self._factories.pop(name, None)
        self._restart_delays.pop(name, None)

    def restart_task(self, name: str) -> None:
        """Manually trigger a restart of a task."""
        if name in self._tasks:
            logger.info(f"Watchdog: manually restarting task '{name}'")
            self._tasks[name].cancel()

    async def start(self) -> None:
        """Start all registered tasks and begin monitoring."""
        self._running = True
        for name, factory in self._factories.items():
            self._start_task(name, factory)
        await self._supervise()

    async def stop(self) -> None:
        """Stop all supervised tasks."""
        self._running = False
        for name, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        logger.info("Watchdog: all tasks stopped")

    def _start_task(self, name: str, factory: Callable[[], Coroutine]) -> None:
        """Start a single task."""
        task = asyncio.create_task(factory(), name=name)
        self._tasks[name] = task
        logger.info(f"Watchdog: started task '{name}'")

    async def _supervise(self) -> None:
        """Monitor loop — checks task health and restarts failed tasks."""
        delays: dict[str, float] = {n: d for n, d in self._restart_delays.items()}

        while self._running:
            for name, task in list(self._tasks.items()):
                if task.done():
                    exc = None
                    try:
                        exc = task.exception()
                    except (asyncio.CancelledError, asyncio.InvalidStateError):
                        pass

                    if exc:
                        logger.error(
                            f"Watchdog: task '{name}' crashed with: {exc}. "
                            f"Restarting in {delays.get(name, 2)}s..."
                        )
                    else:
                        logger.warning(f"Watchdog: task '{name}' exited. Restarting...")

                    # Wait with backoff
                    delay = delays.get(name, 2.0)
                    await asyncio.sleep(delay)

                    # Increase delay (exponential backoff, capped)
                    delays[name] = min(delay * 1.5, self._max_restart_delay)

                    # Restart
                    if name in self._factories:
                        self._start_task(name, self._factories[name])

                        # Reset delay on successful restart
                        delays[name] = self._restart_delays.get(name, 2.0)

            await asyncio.sleep(2)

    def get_task_statuses(self) -> dict[str, str]:
        """Get the status of all supervised tasks."""
        statuses = {}
        for name, task in self._tasks.items():
            if task.done():
                try:
                    exc = task.exception()
                    statuses[name] = f"crashed: {exc}" if exc else "exited"
                except (asyncio.CancelledError, asyncio.InvalidStateError):
                    statuses[name] = "cancelled"
            elif task.cancelled():
                statuses[name] = "cancelled"
            else:
                statuses[name] = "running"
        return statuses
