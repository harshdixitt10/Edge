# Industrial Edge Server — Completed Functionality Tracker

> Last updated: 2026-03-16

---

## ✅ Phase 1: Core Pipeline — COMPLETE

### Config System & Models
- ✅ `core/models.py` — All Pydantic models (DataEvent, OpcuaAdapterConfig with full hierarchy: TagSuffix, ProtocolConnection, ReadTag, WriteTag, SourceTag, MetricMapping, ThingConfig)
- ✅ `core/models.py` — Application config models (ServerConfig, AuthConfig, CloudConfig, DatabaseConfig, LoggingConfig, AppConfig)
- ✅ `core/config_manager.py` — YAML config load/save/validate with auto-directory creation
- ✅ `config.yaml` — Master config with server, auth, cloud, database, logging sections

### Data Pipeline
- ✅ `core/event_bus.py` — asyncio Queue wrapper with publish/subscribe pattern for live dashboard feeds
- ✅ `store/local_store.py` — SQLite WAL-mode store with:
  - Event CRUD (write, mark_sent, mark_sent_bulk, get_unsent, get_recent)
  - Adapter config persistence (save, get, list, delete, status update)
  - User management (create, get)
  - Retention cleanup of old sent events

### Protocol Adapters
- ✅ `adapters/base_adapter.py` — Abstract base class with lifecycle (start/stop/connect/disconnect/run)
- ✅ `adapters/opcua_adapter.py` — OPC-UA adapter using asyncua with:
  - Real subscription mode (data change notifications via SubscriptionHandler)
  - Simulation mode fallback (generates test data when asyncua unavailable)
  - Tag mapping (tag_id → metric_id via metric_mappings)
  - `test_opcua_connection()` function for UI test button

### Cloud Connector
- ✅ `cloud/protocols/http_protocol.py` — HTTPS REST connector with httpx:
  - Health check ping
  - Batch event publishing
  - Heartbeat sending (CPU, memory, uptime, adapter statuses)
  - Full HTTP error handling (400, 401/403, 429, 5xx, timeouts)
  - Exponential backoff reconnection loop (1s → 2s → 4s → ... → max 60s)
- ✅ `cloud/connector.py` — Pipeline orchestrator (bus → store → cloud)
- ✅ `cloud/backfill.py` — Backfill engine:
  - `replay_unsent()` — replays all unsent events in timestamp order
  - `monitor_and_backfill()` — watches for reconnection and auto-triggers replay

### Watchdog & Supervision
- ✅ `core/watchdog.py` — Task supervisor with:
  - Task registration and monitoring
  - Auto-restart of crashed coroutines with exponential backoff
  - Task status reporting for dashboard
  - Graceful stop of all tasks

### Entry Point
- ✅ `main.py` — Wires all services together:
  - Config loading → Store init → Auth setup → Event bus → Cloud connector → Backfill → Web app → Watchdog
  - Graceful shutdown on SIGTERM/SIGINT
  - Startup banner with credentials and URLs
  - Default user creation on first run

---

## ✅ Phase 2: Web UI — COMPLETE

### FastAPI App & Auth
- ✅ `web/app.py` — App factory with JWT auth middleware, dependency injection, login/logout
- ✅ `web/auth.py` — JWT token create/verify, bcrypt password hashing

### Routes
- ✅ `web/routes/dashboard.py` — Dashboard with system stats (CPU, RAM, Disk, Uptime), health endpoint, live status API
- ✅ `web/routes/adapters.py` — Full adapter CRUD: list, create, edit, delete, toggle, test connection, preview JSON
- ✅ `web/routes/settings.py` — Cloud config and retention settings management

### Templates (Premium Dark-Mode UI)
- ✅ `web/templates/base.html` — Design system: CSS variables, sidebar navigation, cards, badges, forms, modals, tables, animations, responsive breakpoints
- ✅ `web/templates/login.html` — Animated background, glassmorphism card, premium styling
- ✅ `web/templates/dashboard.html` — Metric cards (CPU/Memory/Uptime/Disk), cloud/buffer/adapter status, watchdog service badges, live data feed table with auto-refresh
- ✅ `web/templates/adapters.html` — Adapter list with status badges, edit/start/stop/delete actions
- ✅ `web/templates/adapter_select.html` — Protocol type selection (OPC-UA available, Modbus/MQTT coming soon)
- ✅ `web/templates/opcua_config.html` — Full multi-section config form:
  - Section A: Global settings (auto_concurrency, thread pool, schedule delay)
  - Section B: Thing config (name, key, intervals, publish mode, connection, tags)
  - Section C: OPC-UA server connection (URL, security, auth, timeout, test button)
  - Section D: Read tags (dynamic table with add/remove)
  - Section F: Metric mappings (dynamic table)
  - Section G: Tag suffixes with presets (Round to 2 decimals, Null guard)
  - JSON builder, Preview JSON modal, Test Connection
- ✅ `web/templates/settings.html` — Cloud connection, data retention, system info

---

## 📊 Project Structure

```
edge_server/
├── main.py                          # Entry point
├── config.yaml                      # Master config
├── requirements.txt                 # Dependencies
├── core/
│   ├── models.py                    # Pydantic models
│   ├── config_manager.py            # Config load/save/validate
│   ├── event_bus.py                 # asyncio Queue event bus
│   └── watchdog.py                  # Task supervisor
├── adapters/
│   ├── base_adapter.py              # Abstract base class
│   └── opcua_adapter.py             # OPC-UA implementation
├── store/
│   └── local_store.py               # SQLite WAL-mode store
├── cloud/
│   ├── connector.py                 # Pipeline orchestrator
│   ├── backfill.py                  # Backfill engine
│   └── protocols/
│       └── http_protocol.py         # HTTPS REST connector
└── web/
    ├── app.py                       # FastAPI app factory
    ├── auth.py                      # JWT authentication
    ├── routes/
    │   ├── dashboard.py             # Dashboard & health
    │   ├── adapters.py              # Adapter CRUD
    │   └── settings.py              # Settings management
    └── templates/
        ├── base.html                # Base layout + design system
        ├── login.html               # Login page
        ├── dashboard.html           # Dashboard
        ├── adapters.html            # Adapter list
        ├── adapter_select.html      # Adapter type selection
        ├── opcua_config.html        # OPC-UA config form
        └── settings.html            # Settings page
```

---

## 🚀 Quick Start

```bash
cd edge_server
pip install -r requirements.txt
python main.py
# Open http://localhost:8080
# Login: admin / changeme
```
