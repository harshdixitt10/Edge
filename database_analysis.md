# Database Capacity & Backfill Analysis

## Storage Engine

- **SQLite 3** with **WAL (Write-Ahead Logging)** mode
- `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL`
- Crash-resilient — uncommitted data survives process kills
- Single-file database at `data/edge_server.db`

## Event Row Size

Each event row contains:

| Column     | Typical Size |
|------------|-------------|
| id (UUID)  | 36 bytes    |
| adapter    | ~20 bytes   |
| thing_key  | ~20 bytes   |
| node_id    | ~15 bytes   |
| namespace  | 4 bytes     |
| tag_id     | ~20 bytes   |
| metric_id  | ~25 bytes   |
| value      | ~10 bytes   |
| quality    | 4 bytes     |
| timestamp  | 25 bytes    |
| sent       | 1 byte      |
| is_backfill| 1 byte      |
| created_at | 19 bytes    |

**Estimated row size: ~200 bytes** (plus SQLite overhead ~50 bytes = ~250 bytes/row)

## Capacity Estimates for 100 Things

Assumptions:
- 100 things configured
- 50 tags per thing average
- Scan interval: 2 seconds
- Only LATEST value per metric is sent at send_interval (throttled)

### Events Generated Per Day

```
100 things × 50 tags × (86400 / 2) scans/day = 216,000,000 events/day
```

However, with **send-frequency throttling** (default 30s send interval), only the latest value per metric is stored for sending:

```
Stored events = 100 things × 50 tags × (86400 / 30) = 14,400,000 events/day
```

### Storage per Day

```
14,400,000 × 250 bytes = ~3.6 GB/day (worst case, no deletion)
```

### With 7-Day Retention (Default)

```
~3.6 GB × 7 = ~25.2 GB maximum
```

But the cleanup job deletes **sent** events older than 7 days. In normal operation with cloud connected, most events are sent within seconds, so actual storage is much lower:

```
Typical: 500 MB - 2 GB (only unsent events accumulate)
Peak (7-day outage): up to ~25 GB
```

### Recommendations

| Retention | Estimated Max Storage | Use Case |
|-----------|----------------------|----------|
| 7 days    | ~25 GB              | Standard |
| 3 days    | ~11 GB              | Limited disk |
| 14 days   | ~50 GB              | Extended outages |

> **Recommendation**: For 100 things, ensure at least 30 GB free disk space with 7-day retention.

## Backfill Engine

### How It Works

1. **`BackfillEngine.monitor_and_backfill()`** runs as a background watchdog task
2. It polls `HttpCloudConnector.connected` every 2 seconds
3. On **reconnection** (transition from disconnected → connected):
   - Calls `replay_unsent()`
   - Reads unsent events from SQLite in batches (batch_size from config, default 100)
   - Events are ordered by timestamp ASC (oldest first)
   - Each batch is POSTed to cloud via `HttpCloudConnector.publish()`
   - On success, events are marked as `sent = 1` in the database
   - 50ms delay between batches to avoid flooding the cloud
4. On batch failure, backfill stops and retries on next reconnection

### Data Flow

```
Scan → EventBus → LocalStore (immediate write) → SendAggregator → Cloud POST
                        ↓
                  [If offline: events stay unsent in SQLite]
                        ↓
              [On reconnect: BackfillEngine replays all unsent]
```

### Key Properties

- **No data loss**: Events are written to SQLite BEFORE cloud forwarding
- **Ordered replay**: Oldest events are replayed first
- **Batch processing**: Configurable batch size (default 100 events per POST)
- **Back-pressure**: 50ms delay between batches prevents cloud flooding
- **Automatic**: No manual intervention needed — triggered on reconnection

## WAL Checkpoint Recommendations

For high-load scenarios (100+ things), consider adding to initialization:

```python
await self._db.execute("PRAGMA wal_autocheckpoint=1000")  # Checkpoint every 1000 pages
```

This prevents the WAL file from growing unbounded during heavy write periods.
