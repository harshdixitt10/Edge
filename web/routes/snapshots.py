"""
Snapshot routes — create, list, download, upload, rollback configuration snapshots.
"""

from __future__ import annotations

import json
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from core.audit import log_action
from web.auth import require_role

router = APIRouter()
logger = logging.getLogger(__name__)

# Snapshot Backup folder — same level as logs/ and data/ (one above edge_server/)
SNAPSHOT_BACKUP_DIR = Path(__file__).resolve().parent.parent.parent.parent / "Snapshot Backup"

# Set by app factory
templates: Jinja2Templates = None
store = None
config_manager = None
watchdog = None
http_connector = None  # HttpCloudConnector — used to push snapshots via gateway_key


def _save_snapshot_file(name: str, trigger: str, snapshot_json: str) -> None:
    """Save snapshot as a JSON file and keep only the last 10."""
    try:
        SNAPSHOT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace(" ", "_").replace("/", "_")[:50]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"snapshot_{trigger}_{safe_name}_{ts}.json"
        filepath = SNAPSHOT_BACKUP_DIR / filename
        filepath.write_text(snapshot_json, encoding="utf-8")

        # Prune: keep only the newest 10 snapshot files
        snapshots = sorted(SNAPSHOT_BACKUP_DIR.glob("snapshot_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old_file in snapshots[10:]:
            old_file.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to save snapshot file: {e}")


async def capture_snapshot(trigger: str, name: str) -> None:
    """Take a full configuration snapshot and persist it.

    Called automatically from adapter/settings routes on every config change.
    """
    if not store or not config_manager:
        return
    try:
        adapters = await store.get_adapters()
        cfg = config_manager.config

        snapshot = {
            "version": "1.0",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "trigger": trigger,
            "cloud": {
                "endpoint_url": cfg.cloud.endpoint_url,
                "edge_id": cfg.cloud.edge_id,
                "batch_size": cfg.cloud.batch_size,
                "heartbeat_interval_secs": cfg.cloud.heartbeat_interval_secs,
                "timeout_secs": cfg.cloud.timeout_secs,
                "ssl_verify": cfg.cloud.ssl_verify,
                # api_key and secret_key intentionally omitted for security
            },
            "database": {
                "retention_days": cfg.database.retention_days,
            },
            "server": {
                "host": cfg.server.host,
                "port": cfg.server.port,
            },
            "adapters": [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "type": a["type"],
                    "enabled": a["enabled"],
                    "config": json.loads(a["config_json"]),
                }
                for a in adapters
            ],
        }

        snapshot_json_str = json.dumps(snapshot, indent=2)
        await store.save_snapshot(
            snapshot_id=str(uuid.uuid4()),
            name=name,
            trigger=trigger,
            snapshot_json=snapshot_json_str,
        )
        logger.info(f"Snapshot created: {name} (trigger: {trigger})")

        # Save snapshot to file-based backup (keep last 10)
        _save_snapshot_file(name, trigger, snapshot_json_str)

        # Push snapshot to Datonis cloud if gateway_key is configured
        gateway_key = cfg.cloud.gateway_key if hasattr(cfg.cloud, "gateway_key") else ""
        if gateway_key and http_connector:
            import asyncio
            asyncio.create_task(_push_snapshot_to_cloud(gateway_key, snapshot))

    except Exception as e:
        logger.warning(f"Failed to create snapshot: {e}")


async def _push_snapshot_to_cloud(gateway_key: str, snapshot: dict) -> None:
    """Fire-and-forget: send snapshot to Datonis cloud via gateway key."""
    try:
        await http_connector.send_gateway_snapshot(gateway_key, snapshot)
    except Exception as e:
        logger.warning(f"Failed to push snapshot to cloud: {e}")


@router.get("/snapshots", response_class=HTMLResponse)
async def snapshots_page(request: Request):
    """List all saved snapshots."""
    snapshots = await store.get_snapshots() if store else []
    return templates.TemplateResponse("snapshots.html", {
        "request": request,
        "snapshots": snapshots,
    })


@router.get("/api/snapshots/{snapshot_id}/download")
async def download_snapshot(snapshot_id: str):
    """Download a snapshot as a JSON file."""
    if not store:
        return JSONResponse({"error": "Store not available"}, status_code=500)
    snapshot = await store.get_snapshot(snapshot_id)
    if not snapshot:
        return JSONResponse({"error": "Snapshot not found"}, status_code=404)
    filename = f"snapshot-{snapshot['created_at'][:10]}-{snapshot_id[:8]}.json"
    return Response(
        content=snapshot["snapshot_json"],
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/snapshots/upload", dependencies=[Depends(require_role("admin"))])
async def upload_snapshot(request: Request):
    """Upload a snapshot JSON and save it (does NOT apply — use rollback to apply)."""
    if not store:
        return JSONResponse({"success": False, "message": "Store not available"})
    try:
        data = await request.json()
        # Validate it looks like a snapshot
        if "adapters" not in data and "cloud" not in data:
            return JSONResponse({"success": False, "message": "Invalid snapshot format"})
        snap_id = str(uuid.uuid4())
        await store.save_snapshot(
            snapshot_id=snap_id,
            name=f"Uploaded snapshot ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')})",
            trigger="upload",
            snapshot_json=json.dumps(data, indent=2),
        )
        await log_action(
            store, request, action="snapshot_uploaded",
            resource_type="snapshot", resource_id=snap_id,
        )
        return JSONResponse({"success": True, "message": "Snapshot uploaded successfully"})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)})


@router.post("/api/snapshots/{snapshot_id}/rollback", dependencies=[Depends(require_role("admin"))])
async def rollback_snapshot(snapshot_id: str, request: Request):
    """Roll back the entire configuration to a saved snapshot."""
    if not store or not config_manager:
        return JSONResponse({"success": False, "message": "Store or config not available"})
    snapshot = await store.get_snapshot(snapshot_id)
    if not snapshot:
        return JSONResponse({"success": False, "message": "Snapshot not found"})

    try:
        data = json.loads(snapshot["snapshot_json"])

        # Take a 'before rollback' snapshot first
        await capture_snapshot("pre_rollback", f"Before rollback to: {snapshot['name']}")

        # Restore cloud config (excluding secrets)
        if "cloud" in data:
            c = data["cloud"]
            with config_manager.update_config() as cfg:
                if "endpoint_url" in c:
                    cfg.cloud.endpoint_url = c["endpoint_url"]
                if "edge_id" in c:
                    cfg.cloud.edge_id = c["edge_id"]
                if "batch_size" in c:
                    cfg.cloud.batch_size = c["batch_size"]
                if "heartbeat_interval_secs" in c:
                    cfg.cloud.heartbeat_interval_secs = c["heartbeat_interval_secs"]
                if "timeout_secs" in c:
                    cfg.cloud.timeout_secs = c["timeout_secs"]
                if "ssl_verify" in c:
                    cfg.cloud.ssl_verify = c["ssl_verify"]

        # Restore database settings
        if "database" in data:
            with config_manager.update_config() as cfg:
                cfg.database.retention_days = data["database"].get("retention_days", 7)

        # Restore adapters
        if "adapters" in data:
            # Clear existing adapters and replace with snapshot versions
            existing = await store.get_adapters()
            existing_ids = {a["id"] for a in existing}
            snapshot_ids = {a["id"] for a in data["adapters"]}

            # Delete adapters not in snapshot
            for aid in existing_ids - snapshot_ids:
                await store.delete_adapter(aid)

            # Restore/update snapshot adapters
            for a in data["adapters"]:
                await store.save_adapter(
                    adapter_id=a["id"],
                    name=a["name"],
                    adapter_type=a["type"],
                    config_json=json.dumps(a["config"]),
                    enabled=a.get("enabled", True),
                )

        # Restart adapters to pick up restored config
        if watchdog:
            watchdog.restart_task("adapters")

        logger.info(f"🔄 Rolled back to snapshot: {snapshot['name']}")
        await log_action(
            store, request, action="snapshot_rollback",
            resource_type="snapshot", resource_id=snapshot_id,
            details={"name": snapshot["name"]},
        )
        return JSONResponse({"success": True, "message": f"Rolled back to: {snapshot['name']}"})
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        await log_action(
            store, request, action="snapshot_rollback",
            resource_type="snapshot", resource_id=snapshot_id,
            details={"error": str(e)}, result="failure",
        )
        return JSONResponse({"success": False, "message": f"Rollback failed: {e}"})


@router.post("/api/snapshots/manual", dependencies=[Depends(require_role("admin", "operator"))])
async def manual_snapshot(request: Request):
    """Manually take a snapshot."""
    try:
        data = await request.json()
        name = data.get("name", "Manual snapshot")
    except Exception:
        name = "Manual snapshot"
    await capture_snapshot("manual", name)
    return JSONResponse({"success": True})


@router.post("/api/snapshots/{snapshot_id}/delete", dependencies=[Depends(require_role("admin"))])
async def delete_snapshot(snapshot_id: str, request: Request):
    """Delete a snapshot."""
    if store:
        snap = await store.get_snapshot(snapshot_id)
        await store.delete_snapshot(snapshot_id)
        await log_action(
            store, request, action="snapshot_deleted",
            resource_type="snapshot", resource_id=snapshot_id,
            details={"name": snap["name"] if snap else ""},
        )
    return JSONResponse({"success": True})
