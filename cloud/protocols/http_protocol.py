"""
HTTPS Cloud Connector — publishes event batches to Datonis REST API.

Authentication:
  - Health check: plain GET to /api/v3/users/current_time  (no auth headers)
  - Data/Heartbeat POST: X-Access-Key + X-Dtn-Signature (HMAC-SHA256)
  - Signature = HMAC-SHA256(secret_key, json_body_string)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from core.models import DataEvent

logger = logging.getLogger(__name__)


class HttpCloudConnector:
    """HTTPS REST cloud connector for Datonis using httpx async client."""

    def __init__(self, config: dict):
        self.base_url: str = config.get("endpoint_url", "https://api.datonis.io:443")
        self.access_key: str = config.get("api_key", "")
        self.secret_key: str = config.get("secret_key", "")
        self.edge_id: str = config.get("edge_id", "edge-001")
        self.timeout: int = config.get("timeout_secs", 10)
        self.batch_size: int = config.get("batch_size", 100)
        self.heartbeat_interval: int = config.get("heartbeat_interval_secs", 60)
        self.retry_on_status: list[int] = config.get("retry_on_status", [500, 502, 503, 504])
        self.ssl_verify: bool = config.get("ssl_verify", True)
        self._client: Optional[httpx.AsyncClient] = None
        self.connected: bool = False
        self.backfilling: bool = False

    # ── lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            verify=self.ssl_verify,
        )
        logger.info(f"☁️  Cloud connector initialized — endpoint: {self.base_url}")

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self.connected = False

    # ── Datonis HMAC-SHA256 signing ────────────────────────────

    def _sign(self, body_str: str) -> dict:
        """
        Build Datonis auth headers.
        Signature = hex( HMAC-SHA256( secret_key, body_json_string ) )
        """
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            body_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Access-Key": self.access_key,
            "X-Dtn-Signature": sig,
        }

    # ── health check ──────────────────────────────────────────

    async def health_check(self) -> bool:
        """
        Ping /api/v3/users/current_time  (plain GET, no auth).
        The Java reference treats status < 500 as success for connectivity check.
        We treat 200-299 as truly connected.
        """
        if not self._client:
            return False
        try:
            resp = await self._client.get("/api/v3/users/current_time")
            if 200 <= resp.status_code < 300:
                self.connected = True
                logger.info(f"☁️  Cloud health OK  (HTTP {resp.status_code})")
            elif resp.status_code == 401:
                # 401 means the server is reachable but needs auth — mark as reachable
                # The actual POST endpoints use signed auth headers which may work
                self.connected = True
                logger.info(f"☁️  Cloud reachable (HTTP 401 — auth needed for health, POSTs use signed headers)")
            else:
                self.connected = False
                logger.warning(f"☁️  Cloud health FAIL  (HTTP {resp.status_code}: {resp.text[:200]})")
        except (httpx.ConnectError, httpx.TimeoutException, Exception) as exc:
            logger.warning(f"Cloud health check failed: {exc}")
            self.connected = False
        return self.connected

    # ── publish events ────────────────────────────────────────

    async def publish(self, events: list[DataEvent]) -> bool | None:
        """POST a batch of events to /api/v3/things/event.json.

        Returns:
            True  — cloud accepted the batch.
            None  — cloud permanently rejected (400/422): stale/invalid metric IDs.
                    Caller should mark these events as done to unblock the queue.
            False — transient failure (network error, 5xx, rate limit): retry later.
        """
        if not self._client:
            return False

        try:
            # Build Datonis bulk-event payload.
            #
            # Group by (thing_key, timestamp_ms) so that:
            #   - Multiple metrics sampled at the exact same instant are merged into one entry
            #     (correct for live sends where the aggregator emits one batch per send window)
            #   - Events at different timestamps each become their own entry in the array
            #     (correct for backfill — every historical data point is preserved individually)
            #
            # IMPORTANT: Only include events with a registered metric_id.
            #   Derived tags without metric_mappings have no metric_id and must be excluded.
            merged_events: dict[tuple, dict] = {}
            for e in events:
                metric_name = e.metric_id if e.metric_id else e.tag_id

                # Skip events without a registered metric_id (e.g. unmapped derived tags)
                if not e.metric_id:
                    logger.debug(
                        f"Skipping unmapped tag '{e.tag_id}' for thing '{e.thing_key}' "
                        f"(no metric_id — not registered on cloud)"
                    )
                    continue

                ts_ms = int(e.timestamp.timestamp() * 1000)

                # Key on (thing_key, timestamp) — preserves all historical data points
                key = (e.thing_key, ts_ms)
                if key not in merged_events:
                    merged_events[key] = {
                        "thing_key": e.thing_key,
                        "timestamp": ts_ms,
                        "data": {},
                    }
                merged_events[key]["data"][metric_name] = e.value

            # If all events were filtered out, nothing to send
            if not merged_events:
                logger.debug("All events filtered (no mapped metrics) — nothing to send")
                return True  # Not an error, just nothing to send

            datonis_events = list(merged_events.values())

            payload = {
                "events": datonis_events,
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            }

            body = json.dumps(payload, separators=(",", ":"))
            headers = self._sign(body)

            logger.info(
                f"📤 Sending {len(events)} event(s) to Datonis  "
                f"→ POST {self.base_url}/api/v3/things/event.json"
            )
            logger.info(f"Payload: {body}")

            resp = await self._client.post(
                "/api/v3/things/event.json",
                content=body,
                headers=headers,
            )

            if resp.status_code in (200, 201, 202):
                logger.info(f"✅ Datonis accepted {len(events)} event(s).")
                self.connected = True
                return True

            if resp.status_code == 400:
                # Permanent rejection — bad payload structure, won't change on retry
                logger.warning(f"Cloud permanently rejected batch (400): {resp.text[:500]}")
                return None

            if resp.status_code in (401, 403):
                logger.error(f"Cloud auth error ({resp.status_code}): {resp.text[:500]}")
                self.connected = False
                return False

            if resp.status_code == 422:
                # Permanent rejection — metric IDs in payload don't match registered metrics.
                # Common cause: stale buffered events from an old adapter config.
                # Return None so backfill skips these events instead of retrying forever.
                logger.warning(
                    f"Cloud permanently rejected batch (422 — metric mismatch): {resp.text[:500]}"
                )
                return None

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                logger.warning(f"Cloud rate limited — waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return False

            if resp.status_code in self.retry_on_status:
                logger.warning(f"Cloud server error ({resp.status_code}) — will retry")
                self.connected = False
                return False

            logger.warning(f"Cloud unexpected status: {resp.status_code} {resp.text}")
            return False

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.error(f"Cloud publish failed: {exc}")
            self.connected = False
            return False
        except Exception as exc:
            logger.error(f"Cloud publish unexpected error: {exc}")
            self.connected = False
            return False

    # ── heartbeat ─────────────────────────────────────────────

    async def send_heartbeat(
        self,
        adapter_statuses: dict,
        unsent_events: int = 0,
        cpu_percent: float = 0,
        memory_mb: float = 0,
        uptime_secs: int = 0,
    ) -> bool:
        """POST heartbeat to /api/v3/things/heartbeat.json"""
        if not self._client:
            return False

        try:
            payload = {
                "thing_key": self.edge_id,
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                "data": {
                    "uptime_secs": uptime_secs,
                    "unsent_events": unsent_events,
                    "cpu_percent": cpu_percent,
                    "memory_mb": memory_mb,
                },
            }
            body = json.dumps(payload, separators=(",", ":"))
            headers = self._sign(body)

            resp = await self._client.post(
                "/api/v3/things/heartbeat.json",
                content=body,
                headers=headers,
            )
            if resp.status_code in (200, 201, 202):
                logger.debug("💓 Heartbeat sent OK")
                return True
            else:
                logger.warning(f"Heartbeat rejected: {resp.status_code} {resp.text}")
                return False
        except Exception as exc:
            logger.debug(f"Heartbeat failed: {exc}")
            return False

    # ── reconnect loop ────────────────────────────────────────

    async def reconnect_loop(self) -> None:
        """Exponential backoff reconnect loop. Runs forever in background."""
        delay = 1
        while True:
            if not self.connected:
                logger.info(f"Attempting cloud reconnect in {delay}s...")
                await asyncio.sleep(delay)
                if await self.health_check():
                    logger.info("Cloud reconnected ✅")
                    delay = 1
                else:
                    delay = min(delay * 2, 60)
            else:
                await asyncio.sleep(5)
                await self.health_check()
