"""
Backfill Engine — replays unsent events after cloud reconnection.

Reads all unsent rows from LocalStore in timestamp order and
publishes them in batches to the cloud connector.
"""

from __future__ import annotations

import asyncio
import logging

from cloud.protocols.http_protocol import HttpCloudConnector
from store.local_store import LocalStore

logger = logging.getLogger(__name__)


class BackfillEngine:
    """Replays buffered unsent events to the cloud after reconnection."""

    def __init__(self, store: LocalStore, connector: HttpCloudConnector):
        self.store = store
        self.connector = connector
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def replay_unsent(self) -> int:
        """Replay all unsent events in timestamp order.
        
        Called once after cloud reconnection.
        Returns total number of events replayed.
        """
        logger.info("Starting backfill replay...")
        total = 0

        while self._running:
            batch = await self.store.get_unsent(limit=self.connector.batch_size)
            if not batch:
                break

            # Mark events as backfill
            for e in batch:
                e.is_backfill = True

            success = await self.connector.publish(batch)
            if success:
                ids = [e.id for e in batch]
                await self.store.mark_sent_bulk(ids)
                total += len(batch)
                logger.debug(f"Backfill batch sent: {len(batch)} events (total: {total})")
            else:
                logger.warning("Backfill batch failed — will retry on next reconnect")
                break

            # Yield to prevent flooding the cloud
            await asyncio.sleep(0.05)

        logger.info(f"Backfill complete — {total} events replayed")
        return total

    async def monitor_and_backfill(self) -> None:
        """Background task: watches for reconnection and triggers backfill."""
        was_connected = False

        while self._running:
            is_connected = self.connector.connected

            # Trigger backfill on reconnection
            if is_connected and not was_connected:
                logger.info("Cloud reconnected — triggering backfill")
                await self.replay_unsent()

            was_connected = is_connected
            await asyncio.sleep(2)
