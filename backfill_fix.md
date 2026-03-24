# Backfill Fix — Priority Drain Before Normal Operation

**Date:** 2026-03-24
**Issue:** Buffered events (status = "Buffered") were not being sent to the cloud when network connectivity was restored.

---

## Root Cause

### Primary Bug: Race condition in `run_pipeline` vs `replay_unsent`

The pipeline in `cloud/connector.py` decides whether to direct-send or buffer using this condition:

```python
if unsent_count == 0 and self.http.connected:
    # Direct send to cloud
else:
    # Buffer to SQLite store
```

**The race:** `replay_unsent()` processes events in batches. After marking the last batch as sent, `unsent_count` briefly hits `0` inside the database — even though the backfill loop hasn't fully exited yet. During this window, `run_pipeline` could see `unsent_count == 0` and start sending new events **directly**, bypassing the backfill priority order.

This violated the required invariant:
> **"All buffered events must be sent before resuming normal direct sends."**

### Secondary Issues

| Issue | Location | Impact |
|-------|----------|--------|
| `logger.debug(...)` in `monitor_and_backfill` | `backfill.py:114` | Backfill triggers were invisible at INFO log level |
| 2s poll interval | `backfill.py:117` | Slow to respond after network reconnection |
| No log when backfill completes | `backfill.py` | No confirmation that backfill finished |

---

## Fix

### 1. Added `backfilling: bool` flag to `HttpCloudConnector`

**File:** `cloud/protocols/http_protocol.py`

```python
self.connected: bool = False
self.backfilling: bool = False   # <-- ADDED
```

Both `BackfillEngine` and `CloudConnector` share the same `HttpCloudConnector` instance, so this flag is visible to both.

### 2. Set `backfilling = True` for the entire duration of `replay_unsent`

**File:** `cloud/backfill.py`

Wrapped the entire replay loop in `try/finally`:

```python
async def replay_unsent(self) -> int:
    self.connector.backfilling = True          # <-- ADDED: block direct sends
    try:
        while self._running:
            block = await self.store.get_unsent(...)
            if not block:
                break
            # ... send batches, mark sent ...
    finally:
        self.connector.backfilling = False     # <-- ADDED: always released
        logger.info(f"Backfill cycle complete — {total} events replayed")
```

The `finally` block guarantees the flag is always cleared — even if an exception is raised or the loop breaks early due to network failure.

### 3. `run_pipeline` checks `backfilling` flag before direct-sending

**File:** `cloud/connector.py`

```python
# BEFORE:
if unsent_count == 0 and self.http.connected:

# AFTER:
if unsent_count == 0 and self.http.connected and not self.http.backfilling:
```

This means: direct sends are only allowed when **all three** conditions hold:
- No unsent events in the store
- Cloud is reachable
- No active backfill in progress

### 4. Improved observability in `monitor_and_backfill`

**File:** `cloud/backfill.py`

```python
# BEFORE:
logger.debug(f"Detected {unsent} buffered events! Triggering backfill...")
await asyncio.sleep(2)

# AFTER:
logger.info(f"Detected {unsent} buffered events. Starting backfill (direct sends paused)...")
await asyncio.sleep(1)   # 1s instead of 2s for faster response
```

Also added a completion log:
```python
remaining = await self.store.get_unsent_count()
if remaining == 0:
    logger.info("Backfill complete. Resuming normal direct-send pipeline.")
```

---

## Resulting Behaviour After Fix

```
Network goes down:
  run_pipeline → unsent_count > 0 OR connected=False → Case 2: Buffer to SQLite

Network comes back:
  reconnect_loop  → connected = True
  monitor_and_backfill (within 1s) → detects unsent > 0
    → sets connector.backfilling = True
    → starts replay_unsent()

  run_pipeline (concurrent):
    → unsent_count > 0  → Case 2: Buffer             ← correct
    → OR backfilling = True → Case 2: Buffer          ← race condition FIXED

  replay_unsent completes:
    → sets connector.backfilling = False
    → logs "Backfill complete"

  run_pipeline (subsequent):
    → unsent_count = 0, connected = True, backfilling = False
    → Case 1: Direct send                              ← normal operation resumed
```

---

---

## Fix 2 (Root Cause) — Permanent 422 Rejection Blocking Queue Forever

**Date:** 2026-03-24 (second pass after log analysis)

### Root Cause Discovered From Logs

The actual blocking issue was found in the runtime logs:

```
Payload: {"events":[{"thing_key":"eaeaa4e4dd","data":{"tag1":1094,"machine_status":874}}],...}
→ HTTP 422: {"errors":[{"code":"300027","message":"Data sent in the event does not match the metrics associated with the thing/sensor."}]}
```

The DB contained **old events from a previous adapter configuration** with metric IDs `tag1` and `machine_status`. The cloud only knows `machine.status` (the current metric). When backfill fetches a batch, it merges ALL unsent events for a `thing_key` into one payload — mixing old stale keys with valid ones. The cloud rejects the entire payload with 422.

**The original code treated 422 as a transient failure:**
```python
if resp.status_code == 422:
    return False  # break loop → retry in 1s → 422 again → infinite loop
```

This caused **permanent deadlock**: the old stale events could never be sent, so the queue never moved, and valid newer events were also blocked forever.

**Evidence from logs** — live sends with correct metric succeed:
```
Payload: {"data":{"machine.status":1297}} → HTTP 200 ✅
```
Backfill sends with stale metrics fail forever:
```
Payload: {"data":{"tag1":975,"machine_status":874}} → HTTP 422 ♻️ (infinite retry)
```

### Fix Applied

**`cloud/protocols/http_protocol.py`** — Differentiate permanent vs transient failures:

```python
# Return type changed: bool | None
# True  = cloud accepted
# None  = permanent rejection (400/422) — caller should skip, not retry
# False = transient failure (network/5xx) — retry later

if resp.status_code == 400:
    return None   # was: return False

if resp.status_code == 422:
    return None   # was: return False
```

**`cloud/backfill.py`** — Handle `None` (permanent) separately from `False` (transient):

```python
for idx, res in enumerate(results):
    if res is True:
        successful_ids.extend(...)       # accepted — mark sent
    elif res is None:
        permanent_fail_ids.extend(...)   # permanent rejection — clear to unblock
    else:
        has_transient_failure = True     # transient — stop and retry

if permanent_fail_ids:
    await self.store.mark_sent_bulk(permanent_fail_ids)   # ← key: unblocks queue
    logger.warning(f"⚠️ Skipped {len(permanent_fail_ids)} permanently rejected events...")

if has_transient_failure:
    break  # Only stop on transient failures, not permanent ones
```

### Resulting Behaviour

```
Backfill batch 1: ["tag1:1094", "machine_status:874"] → 422 → None
  → mark_sent_bulk(stale_ids)  ← QUEUE UNBLOCKED
  → continue to next batch

Backfill batch 2: ["machine.status:615"] → 200 → True
  → mark_sent_bulk(valid_ids)  ← DATA DELIVERED ✅
  → continue to next batch

Backfill complete. Normal pipeline resumes.
```

---

---

## Fix 3 — Backfill Data Loss: All Historical Events Collapsed Into One

**Date:** 2026-03-25 (third pass — confirmed via log analysis)

### Root Cause Discovered

After Fix 2, backfill ran and the log showed:

```
📤 Sending 29 event(s) to Datonis
Payload: {"events":[{"thing_key":"eaeaa4e4dd","timestamp":1774375603880,"data":{"machine.status":1406}}],...}
✅ Datonis accepted 29 event(s).
```

29 events were sent but only **1 data point appeared on the cloud** — only the latest value. All historical data was lost.

**Root cause in `publish()` merge logic:**

```python
key = e.thing_key                  # ← grouped by thing only
...
merged_events[key]["timestamp"] = max(...)  # ← kept only latest timestamp
merged_events[key]["data"][metric_name] = e.value  # ← overwrote every value
```

This is designed for **live sends** via `SendAggregator` where only the latest value per send window is intentional. But for **backfill**, every historical data point must be its own entry in the `events` array with its own timestamp.

**Confirmed by reference implementation** (`datonis_edge/BulkDataMessage.java`):
```java
// events = Collection<ObjectNode>
// Each ObjectNode is an individual event with its own thing_key, timestamp, data
```
The reference sends each event as a separate object — full historical fidelity preserved.

### Fix Applied

**`cloud/protocols/http_protocol.py`** — Changed merge key from `thing_key` to `(thing_key, timestamp_ms)`:

```python
# BEFORE: collapses all events for same thing into one (loses history)
key = e.thing_key

# AFTER: each unique timestamp gets its own entry (preserves all data points)
key = (e.thing_key, ts_ms)
```

This correctly handles both use cases:
- **Live sends**: multiple metrics for same thing at same instant → same `(thing_key, ts)` key → merged into one entry ✓
- **Backfill**: 29 events at 29 different timestamps → 29 separate keys → 29 entries in `events` array ✓

### Resulting Payload (After Fix)

```json
{
  "events": [
    {"thing_key": "eaeaa4e4dd", "timestamp": 1774375573880, "data": {"machine.status": 615}},
    {"thing_key": "eaeaa4e4dd", "timestamp": 1774375583880, "data": {"machine.status": 1782}},
    ...29 entries, one per historical data point...
  ]
}
```

All 29 events are now visible as individual data points on the Datonis cloud dashboard.

---

## Files Changed (All Fixes)

| File | Change |
|------|--------|
| `edge_server/cloud/protocols/http_protocol.py` | Added `backfilling` flag; 400/422 → `None` (permanent); merge key `thing_key` → `(thing_key, ts_ms)` to preserve all data points |
| `edge_server/cloud/backfill.py` | `try/finally` with `backfilling` flag; `True/None/False` result handling; permanent failures unblock queue; sleep 2s→1s |
| `edge_server/cloud/connector.py` | Added `not self.http.backfilling` to direct-send guard |
