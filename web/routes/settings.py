"""
Settings routes — cloud configuration as well as system settings.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
logger = logging.getLogger(__name__)

# Will be set by app factory
templates: Jinja2Templates = None
config_manager = None
cloud_connector = None


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Render the settings page."""
    config = config_manager.config if config_manager else None
    cloud_connected = cloud_connector.connected if cloud_connector else False
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "config": config,
        "cloud_connected": cloud_connected,
        "success": request.query_params.get("saved") == "1",
    })


@router.post("/settings/cloud")
async def save_cloud_settings(
    request: Request,
    endpoint_url: str = Form(...),
    api_key: str = Form(""),
    secret_key: str = Form(""),
    edge_id: str = Form(...),
    timeout_secs: int = Form(10),
    batch_size: int = Form(100),
    heartbeat_interval_secs: int = Form(60),
    ssl_verify: bool = Form(False),
):
    """Save cloud connection settings and reinitialize the cloud connector."""
    if config_manager:
        with config_manager.update_config() as config:
            config.cloud.endpoint_url = endpoint_url
            if api_key:
                config.cloud.api_key = api_key
            if secret_key:
                config.cloud.secret_key = secret_key
            config.cloud.edge_id = edge_id
            config.cloud.timeout_secs = timeout_secs
            config.cloud.batch_size = batch_size
            config.cloud.heartbeat_interval_secs = heartbeat_interval_secs
            config.cloud.ssl_verify = ssl_verify

        # Reinitialize cloud connector with new credentials
        if cloud_connector:
            try:
                new_config = config_manager.config.cloud.model_dump()
                await cloud_connector.http.stop()
                cloud_connector.http.__init__(new_config)
                await cloud_connector.http.start()
                # Run health check to verify connection
                await cloud_connector.http.health_check()
                logger.info("Cloud connector reinitialized with new settings")
            except Exception as e:
                logger.error(f"Failed to reinitialize cloud connector: {e}")

    return RedirectResponse("/settings?saved=1", status_code=302)


@router.post("/settings/retention")
async def save_retention_settings(
    request: Request,
    retention_days: int = Form(7),
):
    """Save data retention settings."""
    if config_manager:
        cfg = config_manager.config
        cfg.database.retention_days = retention_days
        config_manager.save()
    return RedirectResponse("/settings?saved=1", status_code=302)


@router.post("/api/settings/test-cloud")
async def test_cloud_connection(request: Request):
    """Test cloud connectivity with current or provided credentials."""
    if not cloud_connector:
        return JSONResponse({"success": False, "message": "Cloud connector not initialized"})

    try:
        connected = await cloud_connector.http.health_check()
        if connected:
            return JSONResponse({
                "success": True,
                "message": f"Successfully connected to {cloud_connector.http.base_url}",
            })
        else:
            return JSONResponse({
                "success": False,
                "message": f"Could not connect to {cloud_connector.http.base_url}. Check endpoint URL and credentials.",
            })
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Connection test failed: {str(e)}"})

