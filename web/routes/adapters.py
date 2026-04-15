"""
Adapter management routes — CRUD for protocol adapters.

Uses the plugin registry for dynamic adapter type support.
Saves JSON backups on every config change.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from adapters.registry import get_registry, get_config_model, get_available_adapters
from web.routes import snapshots as snapshots_routes

router = APIRouter()

# Will be set by app factory
templates: Jinja2Templates = None
store = None
watchdog = None
config_manager = None
http_connector = None

# Backup directory — one level above edge_server/
BACKUP_BASE = Path(__file__).resolve().parent.parent.parent.parent


def _save_backup(adapter_type: str, adapter_name: str, config_json: str) -> None:
    """Save a timestamped JSON backup of the adapter config."""
    backup_dir = BACKUP_BASE / "Configuration Backup" / f"{adapter_type}_conf_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_name = adapter_name.replace(" ", "_").replace("/", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = backup_dir / f"{safe_name}_{ts}.json"
    filepath.write_text(json.dumps(json.loads(config_json), indent=2), encoding="utf-8")


# ── Generic adapter list ──────────────────────────────────────

@router.get("/adapters", response_class=HTMLResponse)
async def adapter_list(request: Request):
    adapters = await store.get_adapters() if store else []
    return templates.TemplateResponse("adapters.html", {
        "request": request,
        "adapters": adapters,
    })


@router.get("/adapters/new", response_class=HTMLResponse)
async def adapter_select_type(request: Request):
    available = get_available_adapters()
    return templates.TemplateResponse("adapter_select.html", {
        "request": request,
        "available_adapters": available,
    })


# ── Dynamic config form (works for any registered adapter type) ──

@router.get("/adapters/{adapter_type}/config", response_class=HTMLResponse)
async def adapter_config_new(request: Request, adapter_type: str):
    registry = get_registry()
    info = registry.get(adapter_type)
    if not info:
        return RedirectResponse("/adapters/new", status_code=302)

    ConfigModel = info["config_model"]
    default_config = ConfigModel()
    return templates.TemplateResponse(info["template"], {
        "request": request,
        "adapter_id": "",
        "adapter_name": "",
        "config": default_config.model_dump(),
        "is_edit": False,
    })


@router.get("/adapters/{adapter_id}/edit", response_class=HTMLResponse)
async def adapter_edit(request: Request, adapter_id: str):
    adapter = await store.get_adapter(adapter_id) if store else None
    if not adapter:
        return RedirectResponse("/adapters", status_code=302)

    config = json.loads(adapter["config_json"])
    adapter_type = adapter.get("type", "opcua")
    registry = get_registry()
    info = registry.get(adapter_type)
    template_name = info["template"] if info else "opcua_config.html"
    return templates.TemplateResponse(template_name, {
        "request": request,
        "adapter_id": adapter_id,
        "adapter_name": adapter["name"],
        "config": config,
        "is_edit": True,
    })


@router.post("/adapters/{adapter_type}/save")
async def adapter_save(request: Request, adapter_type: str):
    """Validate and save any adapter type config."""
    form = await request.form()
    adapter_id = form.get("adapter_id") or str(uuid.uuid4())
    adapter_name = form.get("adapter_name", f"{adapter_type.upper()} Adapter")

    registry = get_registry()
    info = registry.get(adapter_type)
    if not info:
        return JSONResponse({"error": f"Unknown adapter type: {adapter_type}"}, status_code=400)

    ConfigModel = info["config_model"]
    template_name = info["template"]

    try:
        config_json_str = form.get("config_json", "{}")
        config_data = json.loads(config_json_str)
        validated = ConfigModel(**config_data)
        config_json = validated.model_dump_json()

        await store.save_adapter(
            adapter_id=adapter_id,
            name=adapter_name,
            adapter_type=adapter_type,
            config_json=config_json,
            enabled=True,
        )

        # Save JSON backup
        _save_backup(adapter_type, adapter_name, config_json)

        if watchdog:
            watchdog.restart_task("adapters")

        await snapshots_routes.capture_snapshot(
            trigger="adapter_saved",
            name=f"Adapter saved: {adapter_name}",
        )
        return RedirectResponse("/adapters", status_code=302)

    except Exception as e:
        config_data_fallback = locals().get("config_data", {})
        return templates.TemplateResponse(template_name, {
            "request": request,
            "adapter_id": adapter_id,
            "adapter_name": adapter_name,
            "config": config_data_fallback,
            "is_edit": bool(form.get("adapter_id")),
            "error": str(e),
        })


# ── Delete / Toggle ───────────────────────────────────────────

@router.post("/adapters/{adapter_id}/delete")
async def adapter_delete(adapter_id: str):
    if store:
        adapter = await store.get_adapter(adapter_id)
        name = adapter["name"] if adapter else adapter_id
        await store.delete_adapter(adapter_id)
        await snapshots_routes.capture_snapshot("adapter_deleted", f"Adapter deleted: {name}")
        if watchdog:
            watchdog.restart_task("adapters")
    return RedirectResponse("/adapters", status_code=302)


@router.post("/adapters/{adapter_id}/toggle")
async def adapter_toggle(adapter_id: str):
    if store:
        adapter = await store.get_adapter(adapter_id)
        if adapter:
            new_enabled = not adapter["enabled"]
            new_status = "stopped" if not new_enabled else "connecting"
            await store.toggle_adapter_enabled(adapter_id, new_enabled, new_status)
            await snapshots_routes.capture_snapshot(
                trigger="adapter_toggled",
                name=f"Adapter {'enabled' if new_enabled else 'disabled'}: {adapter['name']}",
            )
            if watchdog:
                watchdog.restart_task("adapters")
    return RedirectResponse("/adapters", status_code=302)


# ── Import / Test / Sync ──────────────────────────────────────

@router.post("/adapters/{adapter_type}/import", response_class=HTMLResponse)
async def adapter_import(request: Request, adapter_type: str):
    """Re-render config form pre-populated with uploaded JSON."""
    form = await request.form()
    adapter_id = form.get("adapter_id", "")
    adapter_name = form.get("adapter_name", "")
    config_json_str = form.get("config_json", "{}")

    registry = get_registry()
    info = registry.get(adapter_type)
    if not info:
        return RedirectResponse("/adapters/new", status_code=302)

    ConfigModel = info["config_model"]
    try:
        config_data = json.loads(config_json_str)
        validated = ConfigModel(**config_data)
        config = validated.model_dump()
        error = None
    except Exception as e:
        try:
            config = json.loads(config_json_str)
        except Exception:
            config = {}
        error = f"JSON imported but has validation warnings: {e}"

    return templates.TemplateResponse(info["template"], {
        "request": request,
        "adapter_id": adapter_id,
        "adapter_name": adapter_name,
        "config": config,
        "is_edit": bool(adapter_id),
        "error": error,
        "success": None if error else "JSON config imported successfully. Review and save.",
    })


@router.post("/api/adapters/test-connection")
async def test_connection(request: Request):
    """Test connection for adapters that support it."""
    data = await request.json()
    adapter_type = data.pop("_adapter_type", "opcua")
    registry = get_registry()
    info = registry.get(adapter_type)
    if info and "test_connection" in info:
        result = await info["test_connection"](data)
        return JSONResponse(result)
    return JSONResponse({"success": False, "message": f"Test not supported for '{adapter_type}'"})


@router.get("/api/adapters/{adapter_id}/config")
async def get_adapter_config(adapter_id: str):
    if store:
        adapter = await store.get_adapter(adapter_id)
        if adapter:
            return JSONResponse(json.loads(adapter["config_json"]))
    return JSONResponse({"error": "Adapter not found"}, status_code=404)


@router.get("/api/adapters/sync-things")
async def sync_things_from_cloud():
    """Fetch registered Things from Datonis cloud."""
    import logging as _log
    _log.getLogger(__name__).info("sync-things requested")
    if not http_connector:
        return JSONResponse({"success": False, "message": "Cloud connector not available"})
    if not http_connector._client:
        return JSONResponse({"success": False, "message": "HTTP client not started"})
    try:
        things = await http_connector.fetch_things()
        return JSONResponse({"success": True, "things": things, "count": len(things)})
    except Exception as e:
        import traceback
        _log.getLogger(__name__).error(f"sync-things error: {traceback.format_exc()}")
        return JSONResponse({"success": False, "message": str(e)})
