"""
Industrial Edge Server — Main Entry Point

Starts all services:
  - Config loader
  - SQLite local store
  - Event bus
  - OPC-UA adapter(s)
  - Cloud connector + backfill engine
  - FastAPI web server
  - Watchdog supervisor

Usage:
    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

import uvicorn

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core import credential_backup
from core.config_manager import ConfigManager
from core.event_bus import EventBus
from core.watchdog import Watchdog
from cloud.backfill import BackfillEngine
from cloud.connector import CloudConnector
from cloud.protocols.http_protocol import HttpCloudConnector
from store.local_store import LocalStore
from web.app import create_app
from web.auth import AuthManager

logger = logging.getLogger("edge_server")


def setup_logging(config) -> None:
    """Configure logging based on config with rotating file handler."""
    from core.log_handler import CompressedRotatingFileHandler

    log_level = getattr(logging, config.logging.level.upper(), logging.INFO)
    log_file = Path(__file__).parent / config.logging.file

    # Ensure log directory exists
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            CompressedRotatingFileHandler(
                filename=str(log_file),
                maxBytes=1_048_576,   # 1 MB rotation threshold
                backupCount=10,       # Keep last 10 compressed archives
                encoding="utf-8",
            ),
        ],
    )


async def run_web_server(app, host: str, port: int) -> None:
    """Run the FastAPI web server."""
    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()


async def run_adapters(store: LocalStore, bus: EventBus) -> None:
    """Load and run all enabled adapters from the database."""
    from adapters.registry import get_adapter_class
    import json

    async def _run_single(adapter_data):
        try:
            config = json.loads(adapter_data["config_json"])
            adapter_type = adapter_data.get("type", "opcua")
            AdapterClass = get_adapter_class(adapter_type)
            if AdapterClass is None:
                logger.error(
                    f"Unknown adapter type '{adapter_type}' for '{adapter_data['name']}' "
                    f"— plugin folder missing?"
                )
                await store.update_adapter_status(adapter_data["id"], "error")
                return
            adapter = AdapterClass(
                adapter_id=adapter_data["id"],
                name=adapter_data["name"],
                config=config,
                bus=bus,
            )
            await store.update_adapter_status(adapter_data["id"], "connecting")
            try:
                await adapter.start()
            finally:
                await adapter.stop()
                await store.update_adapter_status(adapter_data["id"], "stopped")
        except Exception as e:
            logger.error(f"Failed to start adapter '{adapter_data['name']}': {e}")
            await store.update_adapter_status(adapter_data["id"], "error")

    tasks = []
    adapters_data = await store.get_adapters()
    for adapter_data in adapters_data:
        if not adapter_data["enabled"]:
            continue
        tasks.append(asyncio.create_task(_run_single(adapter_data)))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    else:
        while True:
            await asyncio.sleep(86400)


async def run_cleanup(store: LocalStore, retention_days: int) -> None:
    """Periodic cleanup of old sent events."""
    while True:
        try:
            await store.cleanup_old(days_to_keep=retention_days)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        await asyncio.sleep(3600)  # Run every hour


async def main() -> None:
    """Main entry point — wire everything together and start."""
    print()
    print("=" * 60)
    print("   ⚡ Industrial Edge Server v1.0")
    print("=" * 60)
    print()

    # ── 1. Load Configuration ──
    config_manager = ConfigManager()
    config = config_manager.load()
    setup_logging(config)
    logger.info("Configuration loaded")

    # ── 2. Initialize Local Store ──
    db_path = str(Path(__file__).parent / config.database.path)
    store = LocalStore(db_path)
    await store.initialize()
    logger.info("Local store initialized")

    # ── 3. Ensure Default User Exists ──
    auth_manager = AuthManager(
        secret_key=config.server.secret_key,
        algorithm=config.auth.jwt_algorithm,
        expiry_minutes=config.auth.jwt_expiry_minutes,
    )

    # Only bootstrap the default user on first run (when config has no stored hash).
    # Never overwrite an existing hash — that would silently reset the admin's
    # password to "changeme" on every restart after a credential change.
    if not config.auth.default_password_hash:
        pwd_hash = auth_manager.hash_password("changeme")
        config.auth.default_password_hash = pwd_hash
        config_manager.save()
        credential_backup.write(config.auth.default_username, "changeme")
        logger.info(f"Default user '{config.auth.default_username}' created")

    # Ensure the DB users table matches the config (single source of truth).
    # This wipes stale rows — e.g. the original "admin" entry after a rename —
    # that would otherwise keep working via the fallback lookup in web/app.py.
    await store.sync_default_user(
        config.auth.default_username,
        config.auth.default_password_hash,
    )

    # ── 4. Create Event Bus ──
    bus = EventBus(maxsize=10_000)
    logger.info("Event bus created")

    # ── 5. Create Cloud Connector ──
    cloud_config = config.cloud.model_dump()
    cloud_connector = CloudConnector(cloud_config, bus, store)
    await cloud_connector.start()
    logger.info("Cloud connector initialized")

    # ── 6. Create Backfill Engine ──
    backfill = BackfillEngine(store, cloud_connector.http)
    await backfill.start()
    logger.info("Backfill engine initialized")

    # ── 7. Create Web App ──
    app = create_app(
        config_manager=config_manager,
        store=store,
        bus=bus,
        cloud_connector=cloud_connector,
        watchdog=None,  # Will be set after watchdog creation
        auth_manager=auth_manager,
    )
    logger.info("Web application created")

    # ── 8. Create Watchdog ──
    watchdog = Watchdog()

    # Register all services with watchdog
    watchdog.register("cloud_pipeline", cloud_connector.run_pipeline)
    watchdog.register("cloud_reconnect", cloud_connector.http.reconnect_loop)
    watchdog.register("backfill_monitor", backfill.monitor_and_backfill)
    watchdog.register("adapters", lambda: run_adapters(store, bus))
    watchdog.register("cleanup", lambda: run_cleanup(store, config.database.retention_days))

    # Inject watchdog reference into dashboard and adapters
    from web.routes import dashboard as dashboard_routes
    from web.routes import adapters as adapters_routes
    dashboard_routes.watchdog = watchdog
    adapters_routes.watchdog = watchdog

    logger.info("Watchdog configured with all services")

    # ── 9. Graceful Shutdown Handler ──
    shutdown_event = asyncio.Event()

    def signal_handler(signame):
        logger.info(f"Received {signame}, initiating graceful shutdown...")
        shutdown_event.set()

    # Register signal handlers (works on Unix; on Windows uses different approach)
    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s.name))
    except NotImplementedError:
        # Windows doesn't support add_signal_handler
        signal.signal(signal.SIGINT, lambda s, f: signal_handler("SIGINT"))

    # ── 10. Start Everything ──
    print(f"   🌐 Web UI:   http://localhost:{config.server.port}")
    print(f"   👤 Login:    {config.auth.default_username} / changeme")
    print(f"   📊 API Docs: http://localhost:{config.server.port}/docs")
    print(f"   💾 Database: {db_path}")
    print()
    print("   Press Ctrl+C to stop")
    print("=" * 60)
    print()

    # Run web server and watchdog concurrently
    web_task = asyncio.create_task(
        run_web_server(app, config.server.host, config.server.port)
    )
    watchdog_task = asyncio.create_task(watchdog.start())

    try:
        # Wait for shutdown signal
        await shutdown_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Shutting down...")
        await watchdog.stop()
        await cloud_connector.stop()
        await backfill.stop()
        await store.close()
        web_task.cancel()
        watchdog_task.cancel()
        logger.info("Edge Server stopped cleanly ✅")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚡ Edge Server stopped.")
