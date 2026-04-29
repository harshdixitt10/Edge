"""User management routes (admin-only).

Manages every user EXCEPT the bootstrap admin from config.yaml — that one is
edited via Settings → Change Credentials, which already exists.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from core.audit import log_action
from store.local_store import VALID_ROLES
from web.auth import require_role
from web.routes import snapshots as snapshots_routes

router = APIRouter()
logger = logging.getLogger(__name__)

# Set by app factory
templates: Jinja2Templates = None
store = None
auth_manager = None
config_manager = None


USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _flash_redirect(error: str = "", success: str = ""):
    qs = []
    if error:
        qs.append(f"error={error.replace(' ', '+')}")
    if success:
        qs.append(f"success={success.replace(' ', '+')}")
    suffix = ("?" + "&".join(qs)) if qs else ""
    return RedirectResponse(f"/users{suffix}", status_code=302)


@router.get("/users", response_class=HTMLResponse, dependencies=[Depends(require_role("admin"))])
async def users_page(request: Request):
    db_users = await store.get_users() if store else []

    # The bootstrap admin lives in config.yaml AND in the users table after
    # sync. Mark it so the UI shows "(bootstrap)" and disables Delete / role
    # change for that row — those are managed via Settings.
    bootstrap_username = ""
    if config_manager:
        bootstrap_username = config_manager.config.auth.default_username

    users = []
    for u in db_users:
        users.append({
            **u,
            "is_bootstrap": u["username"] == bootstrap_username,
        })

    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": users,
        "valid_roles": list(VALID_ROLES),
        "bootstrap_username": bootstrap_username,
        "error": request.query_params.get("error", ""),
        "success": request.query_params.get("success", ""),
    })


@router.post("/users/create", dependencies=[Depends(require_role("admin"))])
async def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("viewer"),
):
    username = username.strip()
    if len(username) < 3 or len(username) > 32:
        return _flash_redirect(error="Username must be 3-32 chars")
    if not USERNAME_RE.match(username):
        return _flash_redirect(error="Username may only contain letters, numbers, dot, underscore, hyphen")
    if role not in VALID_ROLES:
        return _flash_redirect(error="Invalid role")
    if len(password) < 6:
        return _flash_redirect(error="Password must be at least 6 characters")

    bootstrap = config_manager.config.auth.default_username if config_manager else ""
    if username == bootstrap:
        return _flash_redirect(error="That username is the bootstrap admin — manage via Settings")

    existing = await store.get_user(username)
    if existing:
        return _flash_redirect(error=f"User '{username}' already exists")

    pwd_hash = auth_manager.hash_password(password)
    await store.create_user(username, pwd_hash, role)
    await snapshots_routes.capture_snapshot("user_created", f"User created: {username} ({role})")
    await log_action(
        store, request, action="user_created",
        resource_type="user", resource_id=username, details={"role": role},
    )
    return _flash_redirect(success=f"User '{username}' created")


@router.post("/users/{username}/role", dependencies=[Depends(require_role("admin"))])
async def update_role(request: Request, username: str, role: str = Form(...)):
    if role not in VALID_ROLES:
        return _flash_redirect(error="Invalid role")

    bootstrap = config_manager.config.auth.default_username if config_manager else ""
    if username == bootstrap:
        return _flash_redirect(error="Cannot change role of the bootstrap admin")

    user = await store.get_user(username)
    if not user:
        return _flash_redirect(error="User not found")

    # Don't let an admin demote themselves if they're the last admin.
    if user["role"] == "admin" and role != "admin":
        admin_count = await store.count_admins()
        caller = getattr(request.state, "user", {}).get("username", "")
        if admin_count <= 1 or caller == username:
            return _flash_redirect(error="Cannot demote the last admin or yourself")

    old_role = user["role"]
    await store.update_user_role(username, role)
    await snapshots_routes.capture_snapshot("user_role_changed", f"User role changed: {username} -> {role}")
    await log_action(
        store, request, action="user_role_changed",
        resource_type="user", resource_id=username,
        details={"from": old_role, "to": role},
    )
    return _flash_redirect(success=f"Role updated for '{username}'")


@router.post("/users/{username}/password", dependencies=[Depends(require_role("admin"))])
async def reset_password(request: Request, username: str, password: str = Form(...)):
    if len(password) < 6:
        return _flash_redirect(error="Password must be at least 6 characters")

    bootstrap = config_manager.config.auth.default_username if config_manager else ""
    if username == bootstrap:
        return _flash_redirect(error="Reset the bootstrap admin password via Settings")

    user = await store.get_user(username)
    if not user:
        return _flash_redirect(error="User not found")

    pwd_hash = auth_manager.hash_password(password)
    await store.update_user_password(username, pwd_hash)
    await log_action(
        store, request, action="user_password_reset",
        resource_type="user", resource_id=username,
    )
    return _flash_redirect(success=f"Password reset for '{username}'")


@router.post("/users/{username}/delete", dependencies=[Depends(require_role("admin"))])
async def delete_user(request: Request, username: str):
    bootstrap = config_manager.config.auth.default_username if config_manager else ""
    if username == bootstrap:
        return _flash_redirect(error="Cannot delete the bootstrap admin")

    caller = getattr(request.state, "user", {}).get("username", "")
    if caller == username:
        return _flash_redirect(error="You cannot delete your own account")

    user = await store.get_user(username)
    if not user:
        return _flash_redirect(error="User not found")

    if user["role"] == "admin":
        admin_count = await store.count_admins()
        if admin_count <= 1:
            return _flash_redirect(error="Cannot delete the last admin")

    await store.delete_user(username)
    await snapshots_routes.capture_snapshot("user_deleted", f"User deleted: {username}")
    await log_action(
        store, request, action="user_deleted",
        resource_type="user", resource_id=username,
        details={"role": user["role"]},
    )
    return _flash_redirect(success=f"User '{username}' deleted")
