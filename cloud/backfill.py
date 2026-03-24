"""
Backfill Engine — replays unsent events after cloud reconnection.

Reads all unsent rows from LocalStore in timestamp order and
publishes them in batches to the cloud connector.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

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
        """Replay all unsent events rapidly in timestamp order.

        Sets connector.backfilling=True for the entire duration so that
        run_pipeline never does a direct send while a backlog is being drained.
        Reads large chunks and partitions them into batches shipped
        asynchronously in parallel, vastly increasing throughput.
        """
        self.connector.backfilling = True
        logger.info("Starting high-throughput backfill replay...")
        total = 0
        concurrency_limit = 10  # How many concurrent HTTP requests to send
        fetch_limit = self.connector.batch_size * concurrency_limit

        try:
            while self._running:
                # Fetch a massive block of unsent events
                block = await self.store.get_unsent(limit=fetch_limit)
                if not block:
                    break

                # Mark all as backfill
                for e in block:
                    e.is_backfill = True

                # Slice into discrete batches based on connector limit
                chunks = [
                    block[i:i + self.connector.batch_size]
                    for i in range(0, len(block), self.connector.batch_size)
                ]

                # Fire all chunks concurrently over network
                tasks = [self.connector.publish(chunk) for chunk in chunks]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                successful_ids = []
                permanent_fail_ids = []
                has_transient_failure = False

                for idx, res in enumerate(results):
                    if res is True:
                        # Cloud accepted — mark sent
                        successful_ids.extend([e.id for e in chunks[idx]])
                    elif res is None:
                        # Permanent rejection (400/422): stale metric IDs or bad payload.
                        # Mark as processed to unblock the queue — these will never be accepted.
                        permanent_fail_ids.extend([e.id for e in chunks[idx]])
                    else:
                        # Transient failure (network error, 5xx) — stop and retry later
                        has_transient_failure = True

                now_utc = datetime.now(timezone.utc).isoformat()

                if successful_ids:
                    await self.store.mark_sent_bulk(successful_ids)
                    thing_keys = set(e.thing_key for e in block if e.id in set(successful_ids))
                    for tk in thing_keys:
                        await self.store.update_activity(
                            thing_key=tk,
                            last_ack_event_ts=now_utc,
                            last_ack_scan_ts=now_utc,
                            last_event_error=""
                        )
                    total += len(successful_ids)
                    logger.info(f"⚡ Backfilled {len(successful_ids)} events (Total replayed: {total})")

                if permanent_fail_ids:
                    # Clear permanently rejected events from the queue so valid events can flow
                    await self.store.mark_sent_bulk(permanent_fail_ids)
                    logger.warning(
                        f"⚠️  Skipped {len(permanent_fail_ids)} permanently rejected events "
                        f"(stale metric IDs from old config — cloud will never accept them). "
                        f"Cleared from queue to unblock backfill."
                    )

                if has_transient_failure:
                    logger.warning("Transient network failure during backfill. Will retry after poll interval.")
                    break  # Retry the whole cycle next time — don't skip transient failures

                # Yield briefly to prevent exhausting CPU
                await asyncio.sleep(0.01)

        finally:
            # Always clear the flag — even if an exception or break occurs
            self.connector.backfilling = False
            logger.info(f"Backfill cycle complete — {total} total events replayed this cycle")

        return total

    async def monitor_and_backfill(self) -> None:
        """Background task: watches for pending backlogs and triggers backfill.

        Priority: drain ALL buffered events before allowing run_pipeline to
        resume direct sends (enforced via connector.backfilling flag).
        """
        while self._running:
            is_connected = self.connector.connected

            if is_connected:
                unsent = await self.store.get_unsent_count()
                if unsent > 0:
                    logger.info(f"Detected {unsent} buffered events. Starting backfill (direct sends paused)...")
                    await self.replay_unsent()
                    remaining = await self.store.get_unsent_count()
                    if remaining == 0:
                        logger.info("Backfill complete. Resuming normal direct-send pipeline.")

            await asyncio.sleep(1)
