"""
Settings routes — cloud configuration as well as system settings.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from core import credential_backup
from core.audit import log_action
from web.auth import require_role
from web.routes import snapshots as snapshots_routes

router = APIRouter()
logger = logging.getLogger(__name__)

# Will be set by app factory
templates: Jinja2Templates = None
config_manager = None
cloud_connector = None
auth_manager = None
store = None


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
        "pw_success": request.query_params.get("pw_saved") == "1",
        "pw_error": request.query_params.get("pw_error", ""),
        "current_username": config.auth.default_username if config else "admin",
    })


@router.post("/settings/cloud", dependencies=[Depends(require_role("admin"))])
async def save_cloud_settings(
    request: Request,
    endpoint_url: str = Form(...),
    api_key: str = Form(""),
    secret_key: str = Form(""),
    gateway_key: str = Form(""),
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
            if gateway_key:
                config.cloud.gateway_key = gateway_key
            config.cloud.edge_id = edge_id
            config.cloud.timeout_secs = timeout_secs
            config.cloud.batch_size = batch_size
            config.cloud.heartbeat_interval_secs = heartbeat_interval_secs
            config.cloud.ssl_verify = ssl_verify

        await snapshots_routes.capture_snapshot("cloud_settings", "Cloud settings updated")
        await log_action(
            store, request, action="settings_cloud_updated",
            resource_type="settings", resource_id="cloud",
            details={
                "endpoint_url": endpoint_url, "edge_id": edge_id,
                "timeout_secs": timeout_secs, "batch_size": batch_size,
                "heartbeat_interval_secs": heartbeat_interval_secs,
                "ssl_verify": ssl_verify,
                "api_key_changed": bool(api_key),
                "secret_key_changed": bool(secret_key),
                "gateway_key_changed": bool(gateway_key),
            },
        )

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


@router.post("/settings/retention", dependencies=[Depends(require_role("admin"))])
async def save_retention_settings(
    request: Request,
    retention_days: int = Form(7),
):
    """Save data retention settings."""
    if config_manager:
        cfg = config_manager.config
        cfg.database.retention_days = retention_days
        config_manager.save()
    await log_action(
        store, request, action="settings_retention_updated",
        resource_type="settings", resource_id="retention",
        details={"retention_days": retention_days},
    )
    return RedirectResponse("/settings?saved=1", status_code=302)


@router.post("/settings/change-credentials", dependencies=[Depends(require_role("admin"))])
async def change_credentials(
    request: Request,
    current_password: str = Form(...),
    new_username: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    """Change the admin username and/or password.

    Username and password are both optional — leave blank to keep unchanged.
    `current_password` is always required for verification.
    If the username changes, the user is logged out so they sign in again.
    """
    if not config_manager or not auth_manager:
        return RedirectResponse("/settings?pw_error=Service+unavailable", status_code=302)

    cfg = config_manager.config.auth

    # Verify current password first
    if not auth_manager.verify_password(current_password, cfg.default_password_hash):
        await log_action(
            store, request, action="credentials_change_attempt",
            resource_type="user", resource_id=cfg.default_username,
            details="wrong_current_password", result="failure",
        )
        return RedirectResponse("/settings?pw_error=Current+password+is+incorrect", status_code=302)

    new_username = new_username.strip()
    username_changed = bool(new_username) and new_username != cfg.default_username
    password_changed = bool(new_password)

    # Nothing to change
    if not username_changed and not password_changed:
        return RedirectResponse("/settings?pw_error=Nothing+to+update+-+provide+a+new+username+or+password", status_code=302)

    # Validate username
    if username_changed:
        if len(new_username) < 3:
            return RedirectResponse("/settings?pw_error=Username+must+be+at+least+3+characters", status_code=302)
        if len(new_username) > 32:
            return RedirectResponse("/settings?pw_error=Username+must+be+32+characters+or+less", status_code=302)
        # Only allow alphanumeric, underscore, dot, hyphen
        import re
        if not re.match(r"^[A-Za-z0-9._-]+$", new_username):
            return RedirectResponse("/settings?pw_error=Username+may+only+contain+letters,+numbers,+dot,+underscore,+hyphen", status_code=302)

    # Validate password
    if password_changed:
        if new_password != confirm_password:
            return RedirectResponse("/settings?pw_error=Passwords+do+not+match", status_code=302)
        if len(new_password) < 6:
            return RedirectResponse("/settings?pw_error=Password+must+be+at+least+6+characters", status_code=302)

    # Capture the OLD username before mutating config so we can clean up its
    # DB row (otherwise it could keep authenticating via the users table).
    old_username = cfg.default_username

    # Apply changes
    with config_manager.update_config() as config:
        if username_changed:
            config.auth.default_username = new_username
        if password_changed:
            config.auth.default_password_hash = auth_manager.hash_password(new_password)

    # Keep the DB users table in sync with the new config:
    #   1. Drop the old bootstrap admin row (if renamed) so the stale name
    #      can't authenticate via the DB lookup path.
    #   2. Upsert the new bootstrap admin with role='admin'.
    if store is not None:
        if username_changed and old_username:
            await store.delete_user(old_username)
        await store.sync_default_user(
            config_manager.config.auth.default_username,
            config_manager.config.auth.default_password_hash,
        )

    # Refresh local plaintext credential backup. We use the new password if it
    # changed, otherwise the just-verified current_password.
    credential_backup.write(
        config_manager.config.auth.default_username,
        new_password if password_changed else current_password,
    )

    await snapshots_routes.capture_snapshot(
        trigger="credentials_changed",
        name=(
            "Admin credentials updated: "
            + ("username + password" if username_changed and password_changed
               else "username" if username_changed else "password")
        ),
    )
    await log_action(
        store, request, action="credentials_changed",
        resource_type="user", resource_id=config_manager.config.auth.default_username,
        details={
            "username_changed": username_changed,
            "password_changed": password_changed,
            "old_username": old_username if username_changed else "",
        },
    )

    # If the username changed, force re-login so the JWT (subject=old username) is replaced
    if username_changed:
        response = RedirectResponse("/login?error=Username+changed+-+please+sign+in+again", status_code=302)
        response.delete_cookie("access_token")
        return response

    return RedirectResponse("/settings?pw_saved=1", status_code=302)


# Backwards-compat alias — the template used to POST to /settings/change-password
@router.post("/settings/change-password")
async def change_password_legacy(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    """Legacy password-only change endpoint. Delegates to change_credentials."""
    return await change_credentials(
        request=request,
        current_password=current_password,
        new_username="",
        new_password=new_password,
        confirm_password=confirm_password,
    )


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

