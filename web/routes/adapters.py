"""
Adapter management routes — CRUD for protocol adapters.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from adapters.opcua_adapter import test_opcua_connection
from core.models import OpcuaAdapterConfig

router = APIRouter()

# Will be set by app factory
templates: Jinja2Templates = None
store = None
watchdog = None


@router.get("/adapters", response_class=HTMLResponse)
async def adapter_list(request: Request):
    """List all configured adapters."""
    adapters = await store.get_adapters() if store else []
    return templates.TemplateResponse("adapters.html", {
        "request": request,
        "adapters": adapters,
    })


@router.get("/adapters/new", response_class=HTMLResponse)
async def adapter_select_type(request: Request):
    """Adapter type selection page."""
    return templates.TemplateResponse("adapter_select.html", {
        "request": request,
    })


@router.get("/adapters/opcua/config", response_class=HTMLResponse)
async def opcua_config_new(request: Request):
    """New OPC-UA adapter configuration form."""
    default_config = OpcuaAdapterConfig()
    return templates.TemplateResponse("opcua_config.html", {
        "request": request,
        "adapter_id": "",
        "adapter_name": "",
        "config": default_config.model_dump(),
        "is_edit": False,
    })


@router.get("/adapters/{adapter_id}/edit", response_class=HTMLResponse)
async def adapter_edit(request: Request, adapter_id: str):
    """Edit an existing adapter configuration."""
    adapter = await store.get_adapter(adapter_id) if store else None
    if not adapter:
        return RedirectResponse("/adapters", status_code=302)

    config = json.loads(adapter["config_json"])
    return templates.TemplateResponse("opcua_config.html", {
        "request": request,
        "adapter_id": adapter_id,
        "adapter_name": adapter["name"],
        "config": config,
        "is_edit": True,
    })


@router.post("/adapters/opcua/save")
async def opcua_save(request: Request):
    """Validate and save OPC-UA adapter configuration."""
    form = await request.form()

    adapter_id = form.get("adapter_id") or str(uuid.uuid4())
    adapter_name = form.get("adapter_name", "OPC-UA Adapter")

    # Build config from form data
    try:
        config_json_str = form.get("config_json", "{}")
        config_data = json.loads(config_json_str)

        # Validate with Pydantic
        validated = OpcuaAdapterConfig(**config_data)
        config_json = validated.model_dump_json()

        await store.save_adapter(
            adapter_id=adapter_id,
            name=adapter_name,
            adapter_type="opcua",
            config_json=config_json,
            enabled=True,
        )

        if watchdog:
            watchdog.restart_task("adapters")

        return RedirectResponse("/adapters", status_code=302)

    except Exception as e:
        return templates.TemplateResponse("opcua_config.html", {
            "request": request,
            "adapter_id": adapter_id,
            "adapter_name": adapter_name,
            "config": config_data if 'config_data' in dir() else {},
            "is_edit": bool(form.get("adapter_id")),
            "error": str(e),
        })


@router.post("/adapters/{adapter_id}/delete")
async def adapter_delete(adapter_id: str):
    """Delete an adapter."""
    if store:
        await store.delete_adapter(adapter_id)
        if watchdog:
            watchdog.restart_task("adapters")
    return RedirectResponse("/adapters", status_code=302)


@router.post("/adapters/{adapter_id}/toggle")
async def adapter_toggle(adapter_id: str):
    """Enable/disable an adapter."""
    if store:
        adapter = await store.get_adapter(adapter_id)
        if adapter:
            new_status = "stopped" if adapter["status"] == "connected" else "connecting"
            await store.update_adapter_status(adapter_id, new_status)
            if watchdog:
                watchdog.restart_task("adapters")
    return RedirectResponse("/adapters", status_code=302)


@router.post("/api/adapters/test-connection")
async def test_connection(request: Request):
    """Test OPC-UA connection (called from config form)."""
    data = await request.json()
    result = await test_opcua_connection(data)
    return JSONResponse(result)


@router.get("/api/adapters/{adapter_id}/config")
async def get_adapter_config(adapter_id: str):
    """Get adapter config as JSON (for Preview JSON button)."""
    if store:
        adapter = await store.get_adapter(adapter_id)
        if adapter:
            return JSONResponse(json.loads(adapter["config_json"]))
    return JSONResponse({"error": "Adapter not found"}, status_code=404)
