# Datonis Edge Server

An **async industrial edge server** that bridges factory-floor devices to the [Datonis IoT cloud platform](https://www.datonis.io). Collects data from OPC-UA, CSV files, and MTConnect agents, buffers locally in SQLite, and forwards to the cloud with HMAC-SHA256 signed HTTPS.

Built with Python, FastAPI, and asyncio. Zero external database dependencies.

---

## Features

- **Multi-Protocol Support** — OPC-UA subscriptions, CSV file polling, MTConnect XML polling
- **Plugin Architecture** — Drop an adapter folder in, restart, it works. Remove it, it's gone
- **Offline Resilience** — SQLite WAL-mode buffer; unsent events replay automatically on reconnect
- **Send Aggregation** — Picks latest value per metric per send interval to throttle cloud traffic
- **Backfill Engine** — Concurrent batch replay of buffered events (10 parallel requests)
- **Watchdog Supervisor** — Auto-restarts crashed tasks with exponential backoff (2s to 30s cap)
- **Web Dashboard** — Real-time status, adapter config UI, activity panel, config snapshots
- **Config Backup** — Every adapter save writes a timestamped JSON backup
- **Derived Tags** — Safe AST-based expression engine for computed metrics (OPC-UA)
- **Dark/Light Theme** — Toggle in the sidebar footer

---

## Quick Start

```bash
cd edge_server
pip install -r requirements.txt
python main.py
```

Open `http://localhost:8090` — login with `admin / changeme`.

---

## Architecture

```
OPC-UA / CSV / MTConnect Devices
        |
        v
  Adapter Plugins  -->  EventBus (asyncio.Queue, 10k)
                              |
                              v
                      CloudConnector
                        |         |
                   SendAggregator  SQLite WAL buffer
                        |
                        v
                HttpCloudConnector (HMAC-SHA256 signed HTTPS)
                        |
                        v
                  Datonis Cloud API
                        |
                  BackfillEngine (replays unsent on reconnect)
```

### Module Map

| Module | Purpose |
|--------|---------|
| `main.py` | Wires all services; startup/shutdown |
| `core/models.py` | Shared Pydantic models (DataEvent, MetricMapping, AppConfig) |
| `core/event_bus.py` | asyncio.Queue pub/sub wrapper |
| `core/watchdog.py` | Task supervisor with auto-restart |
| `core/expression_engine.py` | Safe AST-based expression evaluator |
| `adapters/registry.py` | Plugin auto-discovery |
| `adapters/opcua/` | OPC-UA adapter plugin |
| `adapters/csv/` | CSV file-reader adapter plugin |
| `adapters/mtconnect/` | MTConnect HTTP/XML adapter plugin |
| `store/local_store.py` | SQLite WAL-mode buffer |
| `cloud/connector.py` | Pipeline orchestrator with SendAggregator |
| `cloud/backfill.py` | Concurrent batch replay engine |
| `cloud/protocols/http_protocol.py` | HTTPS REST transport, HMAC-SHA256 signing |
| `web/app.py` | FastAPI factory with JWT middleware |
| `web/routes/` | Dashboard, adapters, settings, activity, snapshots |

---

## Plugin System

Each adapter is a self-contained folder inside `edge_server/adapters/`:

```
adapters/
  base_adapter.py       # Abstract base class (shared)
  registry.py           # Auto-discovers plugins on startup
  opcua/                # Drop this folder in -> OPC-UA available
    __init__.py         # Exports get_adapter_info()
    adapter.py          # OPCUAAdapter class
    models.py           # Config models
  csv/                  # Drop this folder in -> CSV available
    __init__.py
    adapter.py
    models.py
  mtconnect/            # Drop this folder in -> MTConnect available
    __init__.py
    adapter.py
    models.py
```

**Add an adapter:** Copy its folder into `adapters/` and restart the server. It appears in the UI automatically.

**Remove an adapter:** Delete the folder and restart. Gone from the UI. Existing configs of that type show as "error" until re-enabled.

### Writing a New Adapter Plugin

1. Create a folder `adapters/myprotocol/`
2. Create `models.py` with a Pydantic config model (must have `things` list with `thing_key`, `send_interval_ms`, `metric_mappings`)
3. Create `adapter.py` with a class extending `BaseAdapter` — implement `connect()`, `disconnect()`, `run()`
4. Create `__init__.py` with:

```python
def get_adapter_info():
    from adapters.myprotocol.adapter import MyAdapter
    from adapters.myprotocol.models import MyAdapterConfig
    return {
        "type": "myprotocol",
        "name": "My Protocol",
        "description": "Description for the adapter selection page.",
        "adapter_class": MyAdapter,
        "config_model": MyAdapterConfig,
        "template": "myprotocol_config.html",
        "icon_color": "var(--accent-blue)",
    }
```

5. Create the Jinja2 template in `web/templates/myprotocol_config.html`

---

## Supported Adapters

### OPC-UA
- Connects to OPC-UA servers (PLCs, SCADA, DCS)
- Real-time subscriptions via asyncua library
- Supports security policies, username/certificate auth
- Derived tags with JavaScript-like expression engine
- Simulation mode when asyncua is not installed

### CSV File Reader
- Monitors a directory for CSV files matching a glob pattern
- Reads latest row per file at configurable scan intervals
- Column-to-tag mapping with type coercion (number/string/boolean)
- Optional timestamp column parsing
- File change monitoring (only re-reads when mtime changes)

### MTConnect
- Polls MTConnect agent HTTP endpoints (e.g. `/current`)
- Parses XML response with automatic namespace stripping
- Extracts values using XPath expressions
- Filters "UNAVAILABLE" values (MTConnect convention)
- Configurable timeout and SSL verification

---

## Configuration

All configuration is in `edge_server/config.yaml`:

```yaml
server:
  host: 0.0.0.0
  port: 8090
  secret_key: change-me-in-production

cloud:
  endpoint_url: https://api.datonis.io:443
  api_key: "your-api-key"
  secret_key: "your-secret-key"
  gateway_key: "your-gateway-key"
  edge_id: edge-plant-01

database:
  path: ../data/edge_server.db
  retention_days: 7
```

Adapter configurations are managed through the Web UI and stored in SQLite.

---

## Folder Structure

```
Shri_Ganesha/
  edge_server/              # Application code
    adapters/               # Protocol adapter plugins
    cloud/                  # Cloud connectivity
    core/                   # Shared models, config, event bus
    store/                  # SQLite persistence
    web/                    # FastAPI + Jinja2 web UI
    main.py                 # Entry point
    config.yaml             # Runtime configuration
  data/                     # SQLite database
  logs/                     # Rotating log files
  opcua_conf_backup/        # OPC-UA config backups (JSON)
  csv_conf_backup/          # CSV config backups (JSON)
  mtconnect_conf_backup/    # MTConnect config backups (JSON)
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dashboard` | Real-time status dashboard |
| GET | `/adapters` | List all configured adapters |
| GET | `/adapters/new` | Adapter type selection |
| GET | `/adapters/{type}/config` | New adapter config form |
| POST | `/adapters/{type}/save` | Save adapter configuration |
| GET | `/activity` | Thing activity panel |
| GET | `/settings` | Server settings |
| GET | `/snapshots` | Configuration snapshots |
| GET | `/api/adapters/sync-things` | Fetch things from Datonis cloud |
| POST | `/api/adapters/test-connection` | Test adapter connection |

---

## Resilience

| Pattern | Implementation |
|---------|---------------|
| **No data loss** | SQLite WAL mode — events survive crashes |
| **Offline buffering** | Events written to SQLite with `sent=0`, replayed on reconnect |
| **Send throttling** | SendAggregator keeps latest value per metric per send window |
| **Auto-restart** | Watchdog monitors all tasks with exponential backoff (2s to 30s) |
| **Backfill** | 10 concurrent batch requests replay historical events in timestamp order |
| **Config safety** | Timestamped JSON backups on every adapter save |

---

## Requirements

- Python 3.10+
- No external database — SQLite included
- Key dependencies: `fastapi`, `uvicorn`, `httpx`, `aiosqlite`, `pydantic`, `pyyaml`, `bcrypt`, `pyjwt`
- Optional: `asyncua` (for OPC-UA real-time subscriptions)

---

## License

Proprietary — Altizon Systems
