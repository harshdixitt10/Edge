"""
MTConnect Adapter — polls MTConnect agent servers via HTTP and extracts
CNC/machine data using XPath expressions on the XML response.

Reference: datonis_edge com.altizon.gateway.edge.mtconnect
The Java version uses Apache HttpClient + javax.xml.xpath.
This Python port uses httpx (async) + xml.etree.ElementTree (stdlib).
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from adapters.base_adapter import BaseAdapter
from core.event_bus import EventBus
from core.models import DataEvent
from adapters.mtconnect.models import MtConnectAdapterConfig

logger = logging.getLogger(__name__)


class MTConnectAdapter(BaseAdapter):
    """Polls MTConnect XML endpoints and publishes DataEvents."""

    def __init__(self, adapter_id: str, name: str, config: dict, bus: EventBus):
        super().__init__(adapter_id, name, config, bus)
        self._cfg = MtConnectAdapterConfig(**config)
        self._client: httpx.AsyncClient | None = None

    # ── Lifecycle ─────────────────────────────────────────────

    async def connect(self) -> None:
        """Create HTTP client and verify each thing's server is reachable."""
        self._client = httpx.AsyncClient(verify=False)

        for thing in self._cfg.things:
            if thing.disabled:
                continue
            try:
                resp = await self._client.get(
                    thing.server_url, timeout=thing.timeout_secs
                )
                resp.raise_for_status()
                logger.info(
                    f"MTConnect server reachable: {thing.server_url} "
                    f"(HTTP {resp.status_code})"
                )
            except Exception as e:
                raise ConnectionError(
                    f"Cannot reach MTConnect server for thing '{thing.name}' "
                    f"at {thing.server_url}: {e}"
                )

        logger.info(f"MTConnect Adapter '{self.name}': all servers validated")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def run(self) -> None:
        """Polling loop: HTTP GET → parse XML → extract XPath → publish."""
        while self.running:
            for thing in self._cfg.things:
                if thing.disabled:
                    continue
                try:
                    await self._process_thing(thing)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        f"MTConnect Adapter '{self.name}' error on "
                        f"thing '{thing.name}': {e}"
                    )

            intervals = [t.scan_interval_ms for t in self._cfg.things if not t.disabled]
            await asyncio.sleep((min(intervals) if intervals else 5000) / 1000)

    # ── Per-thing processing ──────────────────────────────────

    async def _process_thing(self, thing) -> None:
        """Fetch XML from server and emit one DataEvent per configured tag."""
        resp = await self._client.get(thing.server_url, timeout=thing.timeout_secs)
        resp.raise_for_status()

        root = self._parse_xml(resp.text)
        metric_map = {m.tag_id: m.metric_id for m in thing.metric_mappings}
        now = datetime.now(timezone.utc)

        for tag_cfg in thing.tag_configs:
            raw = self._extract_xpath(root, tag_cfg.tag_path)
            if raw is None:
                logger.debug(f"XPath '{tag_cfg.tag_path}' returned no value")
                continue

            if str(raw).lower() == "unavailable":
                logger.debug(f"Tag '{tag_cfg.tag_id}' value is UNAVAILABLE")
                continue

            value = self._coerce(raw, tag_cfg.value_type)

            event = DataEvent(
                adapter_name=self.name,
                thing_key=thing.thing_key,
                node_id=tag_cfg.tag_path,
                namespace=0,
                tag_id=tag_cfg.tag_id,
                metric_id=metric_map.get(tag_cfg.tag_id, ""),
                value=value,
                quality="Good",
                timestamp=now,
            )
            await self.bus.publish(event)

    # ── XML helpers ───────────────────────────────────────────

    @staticmethod
    def _parse_xml(xml_text: str) -> ET.Element:
        """Parse XML and strip all namespace prefixes so XPath works simply."""
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]
            # Also strip namespaces from attributes
            cleaned = {}
            for k, v in elem.attrib.items():
                cleaned[k.split("}", 1)[-1] if "}" in k else k] = v
            elem.attrib = cleaned
        return root

    @staticmethod
    def _extract_xpath(root: ET.Element, xpath: str) -> str | None:
        """Extract text from XML using an XPath expression.

        Handles:
        - Absolute paths like /MTConnectStreams/Streams/...
        - Attribute predicates like DeviceStream[@name='Mc1']
        - Falls back to a deep tag+attribute search
        """
        path = xpath.strip().lstrip("/")

        # Strip root element name if the path starts with it
        root_tag = root.tag
        if path.startswith(root_tag + "/"):
            path = path[len(root_tag) + 1 :]
        elif path.startswith(root_tag + "["):
            path = path[len(root_tag) :]

        # Attempt 1: direct ElementTree find (supports [@attr='val'] subset)
        try:
            elem = root.find(path)
            if elem is not None and elem.text:
                return elem.text.strip()
        except SyntaxError:
            pass

        # Attempt 2: deep search by the last path segment
        try:
            segments = path.rsplit("/", 1)
            last = segments[-1] if segments else path
            tag_name = last.split("[")[0]

            # Extract attribute predicate if present  [@attr='value']
            attr_match = re.search(r"\[@(\w+)=['\"]([^'\"]+)['\"]\]", last)

            for elem in root.iter():
                if elem.tag != tag_name:
                    continue
                if attr_match:
                    attr_name, attr_val = attr_match.group(1), attr_match.group(2)
                    if elem.get(attr_name) != attr_val:
                        continue
                if elem.text:
                    return elem.text.strip()
        except Exception:
            pass

        return None

    @staticmethod
    def _coerce(raw: str, value_type: str):
        """Coerce a raw XML text value to the configured type."""
        if value_type == "number":
            try:
                f = float(raw)
                return int(f) if f.is_integer() else f
            except (ValueError, TypeError, AttributeError):
                return 0.0
        elif value_type == "boolean":
            return str(raw).strip().lower() in ("true", "1", "yes", "on")
        return str(raw)
