"""
Local Store — SQLite WAL-mode database for event buffering.

Every event is persisted locally before being forwarded to cloud.
Unsent events are replayed during backfill after reconnect.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from core.models import DataEvent

logger = logging.getLogger(__name__)

CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    adapter     TEXT NOT NULL,
    thing_key   TEXT NOT NULL DEFAULT '',
    node_id     TEXT NOT NULL,
    namespace   INTEGER DEFAULT 0,
    tag_id      TEXT DEFAULT '',
    metric_id   TEXT DEFAULT '',
    value       TEXT NOT NULL,
    quality     TEXT NOT NULL DEFAULT 'Good',
    timestamp   TEXT NOT NULL,
    sent        INTEGER DEFAULT 0,
    is_backfill INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

CREATE_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_events_sent ON events(sent, timestamp);
"""

CREATE_ADAPTERS_TABLE = """
CREATE TABLE IF NOT EXISTS adapters (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER DEFAULT 1,
    status      TEXT DEFAULT 'stopped',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
"""

CREATE_CLOUD_CONFIG_TABLE = """
CREATE TABLE IF NOT EXISTS cloud_config (
    id                INTEGER PRIMARY KEY DEFAULT 1,
    protocol          TEXT DEFAULT 'https',
    endpoint          TEXT DEFAULT '',
    credentials_json  TEXT DEFAULT '{}',
    updated_at        TEXT DEFAULT (datetime('now'))
);
"""

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);
"""

CREATE_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    trigger     TEXT NOT NULL DEFAULT 'manual',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

CREATE_ACTIVITY_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS activity_log (
    thing_key           TEXT PRIMARY KEY,
    thing_name          TEXT DEFAULT '',
    adapter_name        TEXT DEFAULT '',
    adapter_id          TEXT DEFAULT '',
    status              TEXT DEFAULT 'idle',
    metrics_count       INTEGER DEFAULT 0,
    last_event_ts       TEXT DEFAULT NULL,
    last_ack_event_ts   TEXT DEFAULT NULL,
    last_scan_ts        TEXT DEFAULT NULL,
    last_ack_scan_ts    TEXT DEFAULT NULL,
    last_alert_ts       TEXT DEFAULT NULL,
    last_ack_alert_ts   TEXT DEFAULT NULL,
    last_registered_error TEXT DEFAULT NULL,
    last_event_error    TEXT DEFAULT NULL,
    last_scan_error     TEXT DEFAULT NULL,
    last_alert_error    TEXT DEFAULT NULL,
    events_sent         INTEGER DEFAULT 0,
    events_pending      INTEGER DEFAULT 0,
    updated_at          TEXT DEFAULT (datetime('now'))
);
"""


class LocalStore:
    """SQLite-based local event store with WAL mode for crash resilience."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open database and create tables."""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        # Enable WAL mode for crash resilience
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")

        await self._db.executescript(
            CREATE_EVENTS_TABLE
            + CREATE_EVENTS_INDEX
            + CREATE_ADAPTERS_TABLE
            + CREATE_CLOUD_CONFIG_TABLE
            + CREATE_USERS_TABLE
            + CREATE_ACTIVITY_LOG_TABLE
            + CREATE_SNAPSHOTS_TABLE
        )
        await self._db.commit()
        logger.info(f"Local store initialized at {self.db_path}")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # ── Event Operations ──────────────────────

    def _event_to_row(self, event: DataEvent) -> tuple:
        """Convert a DataEvent to a tuple of column values for INSERT."""
        return (
            event.id,
            event.adapter_name,
            event.thing_key,
            event.node_id,
            event.namespace,
            event.tag_id,
            event.metric_id,
            json.dumps(event.value) if not isinstance(event.value, str) else event.value,
            event.quality,
            event.timestamp.isoformat(),
            1 if event.sent else 0,
            1 if event.is_backfill else 0,
        )

    async def write_event(self, event: DataEvent) -> None:
        """Write a single event to the buffer."""
        await self._db.execute(
            """INSERT OR REPLACE INTO events
               (id, adapter, thing_key, node_id, namespace, tag_id, metric_id, value, quality, timestamp, sent, is_backfill)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._event_to_row(event),
        )
        await self._db.commit()

    async def write_events_bulk(self, events: list) -> None:
        """Write multiple events in a single transaction (one commit).

        Much faster than calling write_event() in a loop — at 3,600 events
        per flush this reduces 3,600 commits to 1.
        """
        if not events:
            return
        await self._db.executemany(
            """INSERT OR REPLACE INTO events
               (id, adapter, thing_key, node_id, namespace, tag_id, metric_id, value, quality, timestamp, sent, is_backfill)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [self._event_to_row(ev) for ev in events],
        )
        await self._db.commit()

    async def mark_sent(self, event_id: str) -> None:
        """Mark a single event as sent."""
        await self._db.execute("UPDATE events SET sent = 1 WHERE id = ?", (event_id,))
        await self._db.commit()

    async def mark_sent_bulk(self, event_ids: list[str]) -> None:
        """Mark multiple events as sent."""
        placeholders = ",".join("?" for _ in event_ids)
        await self._db.execute(
            f"UPDATE events SET sent = 1 WHERE id IN ({placeholders})", event_ids
        )
        await self._db.commit()

    async def get_unsent(self, limit: int = 500) -> list[DataEvent]:
        """Get unsent events ordered by timestamp (oldest first)."""
        cursor = await self._db.execute(
            "SELECT id, adapter, thing_key, node_id, namespace, tag_id, metric_id, value, quality, timestamp "
            "FROM events WHERE sent = 0 ORDER BY timestamp ASC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        events = []
        for row in rows:
            val = row[7]
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
            events.append(
                DataEvent(
                    id=row[0],
                    adapter_name=row[1],
                    thing_key=row[2],
                    node_id=row[3],
                    namespace=row[4],
                    tag_id=row[5],
                    metric_id=row[6],
                    value=val,
                    quality=row[8],
                    timestamp=datetime.fromisoformat(row[9]),
                    sent=False,
                )
            )
        return events

    async def get_unsent_count(self) -> int:
        """Get count of unsent events."""
        cursor = await self._db.execute("SELECT COUNT(*) FROM events WHERE sent = 0")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_total_count(self) -> int:
        """Get total event count."""
        cursor = await self._db.execute("SELECT COUNT(*) FROM events")
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_recent_events(self, limit: int = 20) -> list[DataEvent]:
        """Get most recent events for the dashboard live feed."""
        cursor = await self._db.execute(
            "SELECT id, adapter, thing_key, node_id, namespace, tag_id, metric_id, value, quality, timestamp, sent "
            "FROM events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        events = []
        for row in rows:
            val = row[7]
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
            events.append(
                DataEvent(
                    id=row[0],
                    adapter_name=row[1],
                    thing_key=row[2],
                    node_id=row[3],
                    namespace=row[4],
                    tag_id=row[5],
                    metric_id=row[6],
                    value=val,
                    quality=row[8],
                    timestamp=datetime.fromisoformat(row[9]),
                    sent=bool(row[10]),
                )
            )
        return events

    async def cleanup_old(self, days_to_keep: int = 7) -> int:
        """Delete old sent events beyond retention period. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM events WHERE sent = 1 AND timestamp < ?", (cutoff,)
        )
        await self._db.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info(f"Cleaned up {deleted} old events (older than {days_to_keep} days)")
        return deleted

    # ── Adapter Config Operations ─────────────

    async def save_adapter(
        self, adapter_id: str, name: str, adapter_type: str, config_json: str, enabled: bool = True
    ) -> None:
        """Save or update an adapter configuration."""
        await self._db.execute(
            """INSERT OR REPLACE INTO adapters (id, name, type, config_json, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (adapter_id, name, adapter_type, config_json, 1 if enabled else 0),
        )
        await self._db.commit()

    async def get_adapters(self) -> list[dict]:
        """Get all adapter configurations."""
        cursor = await self._db.execute(
            "SELECT id, name, type, config_json, enabled, status, created_at, updated_at FROM adapters"
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "config_json": r[3],
                "enabled": bool(r[4]),
                "status": r[5],
                "created_at": r[6],
                "updated_at": r[7],
            }
            for r in rows
        ]

    async def get_adapter(self, adapter_id: str) -> Optional[dict]:
        """Get a single adapter configuration."""
        cursor = await self._db.execute(
            "SELECT id, name, type, config_json, enabled, status, created_at, updated_at FROM adapters WHERE id = ?",
            (adapter_id,),
        )
        r = await cursor.fetchone()
        if not r:
            return None
        return {
            "id": r[0],
            "name": r[1],
            "type": r[2],
            "config_json": r[3],
            "enabled": bool(r[4]),
            "status": r[5],
            "created_at": r[6],
            "updated_at": r[7],
        }

    async def delete_adapter(self, adapter_id: str) -> None:
        """Delete an adapter configuration."""
        await self._db.execute("DELETE FROM adapters WHERE id = ?", (adapter_id,))
        await self._db.commit()

    async def update_adapter_status(self, adapter_id: str, status: str) -> None:
        """Update the runtime status of an adapter."""
        await self._db.execute(
            "UPDATE adapters SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, adapter_id),
        )
        await self._db.commit()

    async def toggle_adapter_enabled(self, adapter_id: str, enabled: bool, status: str) -> None:
        """Enable or disable an adapter and update its status."""
        await self._db.execute(
            "UPDATE adapters SET enabled = ?, status = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if enabled else 0, status, adapter_id),
        )
        await self._db.commit()

    # ── User Operations ───────────────────────

    async def get_user(self, username: str) -> Optional[dict]:
        """Get a user by username."""
        cursor = await self._db.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        )
        r = await cursor.fetchone()
        if not r:
            return None
        return {"id": r[0], "username": r[1], "password_hash": r[2]}

    async def create_user(self, username: str, password_hash: str) -> None:
        """Create a new user."""
        await self._db.execute(
            "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        await self._db.commit()

    # ── Activity Log Operations ───────────────

    async def update_activity(
        self,
        thing_key: str,
        thing_name: str = "",
        adapter_name: str = "",
        adapter_id: str = "",
        status: str = "active",
        metrics_count: Optional[int] = None,
        last_event_ts: Optional[str] = None,
        last_ack_event_ts: Optional[str] = None,
        last_scan_ts: Optional[str] = None,
        last_ack_scan_ts: Optional[str] = None,
        last_alert_ts: Optional[str] = None,
        last_ack_alert_ts: Optional[str] = None,
        last_registered_error: Optional[str] = None,
        last_event_error: Optional[str] = None,
        last_scan_error: Optional[str] = None,
        last_alert_error: Optional[str] = None,
        events_sent: Optional[int] = None,
        events_pending: Optional[int] = None,
    ) -> None:
        """Upsert activity log for a thing."""
        # Build SET clause dynamically for non-None fields
        fields = {"thing_name": thing_name, "adapter_name": adapter_name,
                  "adapter_id": adapter_id, "status": status,
                  "updated_at": datetime.now(timezone.utc).isoformat()}
        if metrics_count is not None:
            fields["metrics_count"] = metrics_count
        if last_event_ts is not None:
            fields["last_event_ts"] = last_event_ts
        if last_ack_event_ts is not None:
            fields["last_ack_event_ts"] = last_ack_event_ts
        if last_scan_ts is not None:
            fields["last_scan_ts"] = last_scan_ts
        if last_ack_scan_ts is not None:
            fields["last_ack_scan_ts"] = last_ack_scan_ts
        if last_alert_ts is not None:
            fields["last_alert_ts"] = last_alert_ts
        if last_ack_alert_ts is not None:
            fields["last_ack_alert_ts"] = last_ack_alert_ts
        if last_registered_error is not None:
            fields["last_registered_error"] = last_registered_error
        if last_event_error is not None:
            fields["last_event_error"] = last_event_error
        if last_scan_error is not None:
            fields["last_scan_error"] = last_scan_error
        if last_alert_error is not None:
            fields["last_alert_error"] = last_alert_error
        if events_sent is not None:
            fields["events_sent"] = events_sent
        if events_pending is not None:
            fields["events_pending"] = events_pending

        columns = ["thing_key"] + list(fields.keys())
        placeholders = ", ".join(["?"] * len(columns))
        update_clause = ", ".join(f"{k} = excluded.{k}" for k in fields.keys())

        await self._db.execute(
            f"""INSERT INTO activity_log ({', '.join(columns)})
               VALUES ({placeholders})
               ON CONFLICT(thing_key) DO UPDATE SET {update_clause}""",
            [thing_key] + list(fields.values()),
        )
        await self._db.commit()

    # ── Snapshot Operations ───────────────────

    async def save_snapshot(self, snapshot_id: str, name: str, trigger: str, snapshot_json: str) -> None:
        """Save a configuration snapshot. Keeps only the last 10."""
        await self._db.execute(
            "INSERT OR REPLACE INTO snapshots (id, name, trigger, snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (snapshot_id, name, trigger, snapshot_json),
        )
        # Prune: keep only 10 most recent
        await self._db.execute(
            "DELETE FROM snapshots WHERE id NOT IN "
            "(SELECT id FROM snapshots ORDER BY created_at DESC LIMIT 10)"
        )
        await self._db.commit()

    async def get_snapshots(self) -> list[dict]:
        """Get all snapshots, newest first."""
        cursor = await self._db.execute(
            "SELECT id, name, trigger, created_at FROM snapshots ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [{"id": r[0], "name": r[1], "trigger": r[2], "created_at": r[3]} for r in rows]

    async def get_snapshot(self, snapshot_id: str) -> Optional[dict]:
        """Get a single snapshot including its full JSON."""
        cursor = await self._db.execute(
            "SELECT id, name, trigger, snapshot_json, created_at FROM snapshots WHERE id = ?",
            (snapshot_id,),
        )
        r = await cursor.fetchone()
        if not r:
            return None
        return {"id": r[0], "name": r[1], "trigger": r[2], "snapshot_json": r[3], "created_at": r[4]}

    async def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete a snapshot."""
        await self._db.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        await self._db.commit()

    async def get_activities(self) -> list[dict]:
        """Get all activity log entries."""
        cursor = await self._db.execute(
            "SELECT thing_key, thing_name, adapter_name, adapter_id, status, "
            "metrics_count, last_event_ts, last_ack_event_ts, "
            "last_scan_ts, last_ack_scan_ts, last_alert_ts, last_ack_alert_ts, "
            "last_registered_error, last_event_error, last_scan_error, last_alert_error, "
            "events_sent, events_pending, updated_at "
            "FROM activity_log ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "thing_key": r[0],
                "thing_name": r[1],
                "adapter_name": r[2],
                "adapter_id": r[3],
                "status": r[4],
                "metrics_count": r[5],
                "last_event_ts": r[6],
                "last_ack_event_ts": r[7],
                "last_scan_ts": r[8],
                "last_ack_scan_ts": r[9],
                "last_alert_ts": r[10],
                "last_ack_alert_ts": r[11],
                "last_registered_error": r[12],
                "last_event_error": r[13],
                "last_scan_error": r[14],
                "last_alert_error": r[15],
                "events_sent": r[16],
                "events_pending": r[17],
                "updated_at": r[18],
            }
            for r in rows
        ]


