"""
CSV Adapter — Reads data from CSV files and publishes DataEvents to the EventBus.

Supports:
- Directory monitoring with glob file filter
- Configurable delimiter
- Column-to-tag mapping with type coercion (number / string / boolean)
- File change monitoring (only re-reads when mtime changes)
- Optional timestamp column parsing
- File path as tag value (metadata use-case)

Reference: datonis_edge CSVAdapterImpl.java
"""

from __future__ import annotations

import asyncio
import csv
import glob
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from adapters.base_adapter import BaseAdapter
from core.event_bus import EventBus
from core.models import CsvAdapterConfig, DataEvent

logger = logging.getLogger(__name__)


class CSVAdapter(BaseAdapter):
    """Reads CSV files from monitored directories and publishes DataEvents."""

    def __init__(self, adapter_id: str, name: str, config: dict, bus: EventBus):
        super().__init__(adapter_id, name, config, bus)
        self._cfg = CsvAdapterConfig(**config)
        # filepath -> last mtime (used for monitor_file_updates mode)
        self._file_mtimes: dict[str, float] = {}

    # ──────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Validate that all configured directories exist."""
        for thing in self._cfg.things:
            if thing.disabled:
                continue
            dir_path = Path(thing.directory_url)
            if not dir_path.exists():
                raise FileNotFoundError(
                    f"CSV directory not found for thing '{thing.name}': {dir_path}"
                )
            if not dir_path.is_dir():
                raise NotADirectoryError(
                    f"Path is not a directory for thing '{thing.name}': {dir_path}"
                )
        logger.info(f"CSV Adapter '{self.name}': all directories validated")

    async def disconnect(self) -> None:
        """Clear mtime tracking state."""
        self._file_mtimes.clear()

    async def run(self) -> None:
        """Main polling loop: scan CSV files and publish DataEvents."""
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
                        f"CSV Adapter '{self.name}' error on thing '{thing.name}': {e}"
                    )

            # Sleep for the shortest scan interval across all active things
            intervals = [t.scan_interval_ms for t in self._cfg.things if not t.disabled]
            await asyncio.sleep((min(intervals) if intervals else 5000) / 1000)

    # ──────────────────────────────────────────────────────────────
    # Per-thing processing
    # ──────────────────────────────────────────────────────────────

    async def _process_thing(self, thing) -> None:
        """Find all CSV files matching the thing's filter and process each."""
        dir_path = Path(thing.directory_url)
        pattern = str(dir_path / thing.file_filter)
        files = sorted(glob.glob(pattern))

        if not files:
            logger.debug(f"No files matching '{thing.file_filter}' in '{dir_path}'")
            return

        for filepath in files:
            try:
                await self._process_file(filepath, thing)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error processing '{filepath}': {e}")

    async def _process_file(self, filepath: str, thing) -> None:
        """Read a CSV file and emit a DataEvent per configured tag from the latest row."""
        # In monitor mode, skip files whose mtime hasn't changed
        if thing.monitor_file_updates:
            try:
                current_mtime = os.path.getmtime(filepath)
            except OSError:
                return
            if self._file_mtimes.get(filepath) == current_mtime:
                return
            self._file_mtimes[filepath] = current_mtime

        # Read file in a thread executor so we don't block the event loop
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, self._read_csv_rows, filepath, thing.delimiter, thing.has_header
        )

        if not rows:
            return

        latest_row = rows[-1]
        now = datetime.now(timezone.utc)

        # Try to parse timestamp from a dedicated column if configured
        ts = now
        if thing.timestamp_column and thing.timestamp_column in latest_row:
            try:
                parsed = datetime.fromisoformat(latest_row[thing.timestamp_column])
                ts = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ts = now

        # Build tag_id -> metric_id lookup from metric_mappings
        metric_map = {m.tag_id: m.metric_id for m in thing.metric_mappings}

        for tag_cfg in thing.tag_configs:
            if tag_cfg.is_file_path:
                value = filepath
            elif tag_cfg.column_name in latest_row:
                value = self._coerce(latest_row[tag_cfg.column_name], tag_cfg.value_type)
            else:
                logger.warning(
                    f"Column '{tag_cfg.column_name}' not found in '{filepath}' "
                    f"(available: {list(latest_row.keys())})"
                )
                continue

            event = DataEvent(
                adapter_name=self.name,
                thing_key=thing.thing_key,
                node_id=f"{filepath}:{tag_cfg.column_name}",
                namespace=0,
                tag_id=tag_cfg.tag_id,
                metric_id=metric_map.get(tag_cfg.tag_id, ""),
                value=value,
                quality="Good",
                timestamp=ts,
            )
            await self.bus.publish(event)

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _read_csv_rows(filepath: str, delimiter: str, has_header: bool) -> list[dict]:
        """Blocking CSV reader — intended to run inside a thread executor."""
        rows: list[dict] = []
        try:
            with open(filepath, newline="", encoding="utf-8") as f:
                if has_header:
                    reader = csv.DictReader(f, delimiter=delimiter)
                    for row in reader:
                        rows.append(dict(row))
                else:
                    reader = csv.reader(f, delimiter=delimiter)
                    for row in reader:
                        rows.append({str(j): v for j, v in enumerate(row)})
        except Exception as exc:
            logger.error(f"Failed to read CSV file '{filepath}': {exc}")
        return rows

    @staticmethod
    def _coerce(raw: str, value_type: str):
        """Coerce a raw CSV string to the configured Python type."""
        if value_type == "number":
            try:
                f = float(raw)
                return int(f) if f.is_integer() else f
            except (ValueError, TypeError, AttributeError):
                return 0.0
        elif value_type == "boolean":
            return str(raw).strip().lower() in ("true", "1", "yes", "on")
        return str(raw)  # default: string
