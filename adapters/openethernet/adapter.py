"""
OpenEthernet Adapter — polls machine controllers over a raw TCP socket.

Reference: datonis_edge com.altizon.gateway.edge.openethernet (Java/OSGi)
The Java version uses java.net.Socket + BufferedReader/PrintWriter and a
per-byte read loop. This Python port uses asyncio streams (open_connection)
to keep a single event loop.

Framing protocol per the Java reference:
  prefix = comma-separated decimal ASCII bytes, e.g. "13,10,2"
  suffix = comma-separated decimal ASCII bytes, e.g. "23,13,10,62"

  - The LAST byte of `prefix` is the start-of-payload marker.
  - The LAST byte of `suffix` is the end-of-payload marker.
  - All other prefix/suffix bytes are framing bytes that may appear in the
    stream and must be ignored when reading the payload.

  Read loop (per tag command):
    write command + "\\n", flush
    while reading bytes from the socket:
      - if byte is in skip-set → continue
      - if byte == start_byte  → reset payload buffer, continue
      - if byte == end_byte    → stop, value = payload buffer
      - else                   → append (as ASCII char) to payload

  Validation: the raw decimal-comma-joined record of every byte read for the
  tag must start with the literal `prefix` string and end with `suffix`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from adapters.base_adapter import BaseAdapter
from adapters.openethernet.models import OpenethernetAdapterConfig, OpenethernetThingConfig
from core.event_bus import EventBus
from core.models import DataEvent

logger = logging.getLogger(__name__)


class _ThingFraming:
    """Pre-computed framing parameters for a single thing."""

    __slots__ = ("prefix", "suffix", "start_byte", "end_byte", "skip_bytes")

    def __init__(self, prefix: str, suffix: str):
        self.prefix = prefix
        self.suffix = suffix
        prefix_bytes = [int(b.strip()) for b in prefix.split(",") if b.strip()]
        suffix_bytes = [int(b.strip()) for b in suffix.split(",") if b.strip()]
        if not prefix_bytes or not suffix_bytes:
            raise ValueError("prefix and suffix must each contain at least one byte")
        self.start_byte = prefix_bytes[-1]
        self.end_byte = suffix_bytes[-1]
        # All other framing bytes are noise to be skipped mid-stream
        self.skip_bytes = set(prefix_bytes[:-1]) | set(suffix_bytes[:-1])


class _ThingConnection:
    """Live socket pair (reader/writer) for one thing."""

    __slots__ = ("reader", "writer")

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


class OpenethernetAdapter(BaseAdapter):
    """Polls TCP-socket machines using the OpenEthernet command/response framing."""

    def __init__(self, adapter_id: str, name: str, config: dict, bus: EventBus):
        super().__init__(adapter_id, name, config, bus)
        self._cfg = OpenethernetAdapterConfig(**config)
        self._connections: dict[str, _ThingConnection] = {}
        self._framings: dict[str, _ThingFraming] = {}

    # ── Lifecycle ─────────────────────────────────────────────

    async def connect(self) -> None:
        """Open a socket per enabled thing and pre-compute its framing."""
        for thing in self._cfg.things:
            if thing.disabled:
                continue
            self._framings[thing.thing_key] = _ThingFraming(thing.prefix, thing.suffix)
            try:
                await self._open_socket(thing)
            except Exception as e:
                # Don't fail the whole adapter on one bad thing — log and let
                # the per-cycle reconnect path retry on the next scan.
                logger.error(
                    f"OpenEthernet '{self.name}': could not open socket to "
                    f"{thing.ip_address}:{thing.port} for thing "
                    f"'{thing.name or thing.thing_key}': {e}"
                )
        if not self._connections and any(not t.disabled for t in self._cfg.things):
            raise ConnectionError("OpenEthernet: no thing socket could be opened")
        logger.info(
            f"OpenEthernet '{self.name}': {len(self._connections)} connection(s) ready"
        )

    async def disconnect(self) -> None:
        for thing_key, conn in list(self._connections.items()):
            await conn.close()
        self._connections.clear()
        self._framings.clear()

    async def run(self) -> None:
        while self.running:
            for thing in self._cfg.things:
                if thing.disabled:
                    continue
                try:
                    await self._poll_thing(thing)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        f"OpenEthernet '{self.name}' poll error on thing "
                        f"'{thing.name or thing.thing_key}': {e}"
                    )
                    await self._close_thing(thing.thing_key)
            intervals = [t.scan_interval_ms for t in self._cfg.things if not t.disabled]
            await asyncio.sleep((min(intervals) if intervals else 5000) / 1000)

    # ── Socket plumbing ───────────────────────────────────────

    async def _open_socket(self, thing: OpenethernetThingConfig) -> _ThingConnection:
        await self._close_thing(thing.thing_key)
        logger.info(
            f"OpenEthernet '{self.name}': opening socket to "
            f"{thing.ip_address}:{thing.port}"
        )
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(thing.ip_address, thing.port),
            timeout=thing.timeout_ms / 1000,
        )
        conn = _ThingConnection(reader, writer)
        self._connections[thing.thing_key] = conn
        return conn

    async def _close_thing(self, thing_key: str) -> None:
        conn = self._connections.pop(thing_key, None)
        if conn is not None:
            await conn.close()

    async def _ensure_socket(self, thing: OpenethernetThingConfig) -> _ThingConnection:
        conn = self._connections.get(thing.thing_key)
        if conn is None or conn.reader.at_eof() or conn.writer.is_closing():
            conn = await self._open_socket(thing)
        return conn

    # ── Polling ───────────────────────────────────────────────

    async def _poll_thing(self, thing: OpenethernetThingConfig) -> None:
        if not thing.tag_configs:
            return
        framing = self._framings.get(thing.thing_key)
        if framing is None:
            framing = _ThingFraming(thing.prefix, thing.suffix)
            self._framings[thing.thing_key] = framing

        conn = await self._ensure_socket(thing)
        timeout_s = thing.timeout_ms / 1000

        # Drain any leftover bytes left in the buffer between cycles. The Java
        # reference logs this as an ideally-shouldn't-happen safeguard.
        await self._drain_garbage(conn.reader)

        metric_map = {m.tag_id: m.metric_id for m in thing.metric_mappings}
        now = datetime.now(timezone.utc)

        for tag_cfg in thing.tag_configs:
            command = tag_cfg.command
            try:
                # Write command terminated by newline and flush
                conn.writer.write((command + "\n").encode("ascii", errors="replace"))
                await conn.writer.drain()

                payload, raw_record = await asyncio.wait_for(
                    self._read_framed_response(conn.reader, framing),
                    timeout=timeout_s,
                )
            except (asyncio.TimeoutError, ConnectionError, OSError) as e:
                logger.warning(
                    f"OpenEthernet '{self.name}' tag '{command}' transport error: {e}"
                )
                # Drop socket so next cycle reconnects
                await self._close_thing(thing.thing_key)
                return

            # Validate the framing — exact same check as the Java reference.
            if not raw_record.startswith(framing.prefix) or not raw_record.endswith(framing.suffix):
                logger.warning(
                    f"OpenEthernet '{self.name}' tag '{command}' frame mismatch — "
                    f"got '{raw_record}', skipping cycle"
                )
                # Treat as transient — close and let next cycle reopen.
                await self._close_thing(thing.thing_key)
                return

            value = self._coerce(payload, tag_cfg.value_type)
            event = DataEvent(
                adapter_name=self.name,
                thing_key=thing.thing_key,
                node_id=f"{thing.ip_address}:{thing.port}/{command}",
                namespace=0,
                tag_id=tag_cfg.tag_id,
                metric_id=metric_map.get(tag_cfg.tag_id, ""),
                value=value,
                quality="Good",
                timestamp=now,
            )
            await self.bus.publish(event)

    @staticmethod
    async def _drain_garbage(reader: asyncio.StreamReader) -> None:
        """Best-effort drain of any pending bytes already in the receive buffer."""
        # asyncio.StreamReader has no `ready()` — peek with a non-blocking read.
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=0.001)
                if not chunk:
                    return
        except asyncio.TimeoutError:
            return

    @staticmethod
    async def _read_framed_response(
        reader: asyncio.StreamReader, framing: _ThingFraming
    ) -> tuple[str, str]:
        """Read bytes until the end-of-payload marker, returning (payload, raw_record).

        `raw_record` is the comma-joined decimal representation of every byte read
        — used to validate prefix/suffix framing exactly the way the Java code does.
        """
        payload_chars: list[str] = []
        raw_bytes: list[int] = []
        capturing = False

        while True:
            b = await reader.read(1)
            if not b:
                raise ConnectionError("input stream closed")
            n = b[0]
            raw_bytes.append(n)

            if n in framing.skip_bytes:
                continue
            if n == framing.start_byte:
                payload_chars = []
                capturing = True
                continue
            if n == framing.end_byte:
                break
            if capturing:
                payload_chars.append(chr(n))

        payload = "".join(payload_chars)
        raw_record = ",".join(str(b) for b in raw_bytes)
        return payload, raw_record

    @staticmethod
    def _coerce(raw: str, value_type: str):
        if value_type == "number":
            try:
                f = float(raw.strip())
                return int(f) if f.is_integer() else f
            except (ValueError, TypeError, AttributeError):
                return 0.0
        if value_type == "boolean":
            return str(raw).strip().lower() in ("true", "1", "yes", "on")
        return str(raw)
