"""
Cloud Connector — main orchestrator for cloud communication.

Consumes events from the EventBus, writes them to LocalStore,
and forwards them to the cloud via HttpCloudConnector.

Send-frequency throttling:
  Events are scanned at scan_interval_ms and stored locally (never lost).
  But they are only SENT to the cloud at send_interval_ms for each thing.
  Within each send window, only the LATEST value per metric is sent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Optional

from cloud.protocols.http_protocol import HttpCloudConnector
from core.event_bus import EventBus
from core.models import DataEvent
from store.local_store import LocalStore

logger = logging.getLogger(__name__)


class SendAggregator:
    """Buffers events per (thing_key, metric) and flushes at send_interval.

    For each thing, keeps the latest value per metric.
    On flush (when send_interval elapses), produces one DataEvent per metric
    with the most recent value.
    """

    def __init__(self):
        # thing_key -> send_interval_secs (loaded from adapter configs)
        self._send_intervals: dict[str, float] = {}
        # thing_key -> last flush timestamp
        self._last_flush: dict[str, float] = {}
        # thing_key -> metric_key -> list of DataEvents (buffered)
        self._buffers: dict[str, dict[str, list[DataEvent]]] = defaultdict(lambda: defaultdict(list))

    def set_send_interval(self, thing_key: str, interval_ms: int) -> None:
        """Register the send interval for a thing."""
        self._send_intervals[thing_key] = interval_ms / 1000.0

    def add_event(self, event: DataEvent) -> None:
        """Buffer an event for aggregation."""
        metric_key = event.metric_id if event.metric_id else event.tag_id
        self._buffers[event.thing_key][metric_key].append(event)

        # Initialize last flush time on first event for this thing
        if event.thing_key not in self._last_flush:
            self._last_flush[event.thing_key] = time.monotonic()

    def get_ready_events(self) -> list[DataEvent]:
        """Return events that are ready to be sent based on send intervals.

        For each thing whose send interval has elapsed:
          - Pick the LATEST value per metric (most recent timestamp)
          - If latest is missing, use second-latest
          - Clear the buffer for that thing
        """
        ready: list[DataEvent] = []
        now = time.monotonic()

        for thing_key, metrics in list(self._buffers.items()):
            interval = self._send_intervals.get(thing_key, 30.0)  # default 30s
            last = self._last_flush.get(thing_key, now)

            if (now - last) >= interval:
                # Time to send — pick latest value per metric
                for metric_key, events in metrics.items():
                    if not events:
                        continue
                    # Sort by timestamp descending, pick the latest
                    events.sort(key=lambda e: e.timestamp, reverse=True)
                    latest = events[0]
                    ready.append(latest)

                # Clear buffer and update flush time
                self._buffers[thing_key] = defaultdict(list)
                self._last_flush[thing_key] = now

        return ready

    def has_pending(self) -> bool:
        """Check if there are any buffered events."""
        return any(
            any(events for events in metrics.values())
            for metrics in self._buffers.values()
        )


class CloudConnector:
    """Orchestrates event flow: bus → store → cloud (with send-frequency throttling)."""

    def __init__(self, config: dict, bus: EventBus, store: LocalStore):
        self.config = config
        self.bus = bus
        self.store = store
        self.http = HttpCloudConnector(config)
        self._running = False
        self._aggregator = SendAggregator()

    async def start(self) -> None:
        """Initialize the cloud connector."""
        await self.http.start()
        self._running = True

    async def stop(self) -> None:
        """Stop the cloud connector."""
        self._running = False
        await self.http.stop()

    @property
    def connected(self) -> bool:
        return self.http.connected

    async def load_send_intervals(self) -> None:
        """Load send intervals from adapter configs in the database."""
        try:
            adapters = await self.store.get_adapters()
            for adapter_data in adapters:
                if not adapter_data["enabled"]:
                    continue
                try:
                    config = json.loads(adapter_data["config_json"])
                    for thing in config.get("thing_configs", []):
                        thing_key = thing.get("thing_key", "")
                        send_interval = thing.get("send_interval_ms", 30000)
                        if thing_key:
                            self._aggregator.set_send_interval(thing_key, send_interval)
                            logger.debug(
                                f"📋 Send interval for thing '{thing_key}': "
                                f"{send_interval}ms"
                            )
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Failed to parse adapter config: {e}")
        except Exception as e:
            logger.warning(f"Failed to load send intervals: {e}")

    async def _handle_config_updates(self):
        """Periodically reload send intervals to pick up user edits."""
        while self._running:
            try:
                await self.load_send_intervals()
            except Exception:
                pass
            await asyncio.sleep(10)  # Check for updates every 10 seconds

    async def run_pipeline(self) -> None:
        """Main pipeline: consume events from bus → aggregate → store + send at interval.

        Only the LATEST value per metric is stored and sent at each send interval.
        Intermediate scan values are NOT persisted — they are held in memory only.
        This prevents unnecessary storage when scan_interval < send_interval.
        """
        # Load send intervals from adapter configs
        await self.load_send_intervals()
        
        # Start the background task to reload configs periodically
        asyncio.create_task(self._handle_config_updates())

        while self._running:
            try:
                # Consume events with a timeout to allow periodic flushing
                try:
                    event = await asyncio.wait_for(self.bus.consume(), timeout=1.0)
                    self.bus.task_done()

                    # Load send interval for unknown things on-the-fly
                    if event.thing_key not in self._aggregator._send_intervals:
                        await self.load_send_intervals()

                    # Buffer in aggregator only (NOT written to store yet)
                    self._aggregator.add_event(event)

                    logger.info(
                        f"📥 Event received — thing={event.thing_key} "
                        f"metric={event.metric_id or event.tag_id} "
                        f"value={event.value}"
                    )
                except asyncio.TimeoutError:
                    pass

                # Check if any things have events ready to send (send interval elapsed)
                ready_events = self._aggregator.get_ready_events()
                if ready_events:
                    # Write ONLY the aggregated (latest) events to store
                    for ev in ready_events:
                        await self.store.write_event(ev)

                    if self.http.connected:
                        success = await self.http.publish(ready_events)
                        if success:
                            ids = [e.id for e in ready_events]
                            await self.store.mark_sent_bulk(ids)
                            logger.info(
                                f"📤 Sent {len(ready_events)} aggregated event(s) to cloud"
                            )
                    else:
                        logger.info(
                            f"💾 Stored {len(ready_events)} aggregated event(s) locally (cloud offline)"
                        )

            except Exception as e:
                logger.error(f"Pipeline error: {e}")
                await asyncio.sleep(1)

