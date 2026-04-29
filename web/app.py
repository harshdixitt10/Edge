"""
FastAPI Application Factory — creates and configures the web server.

Sets up Jinja2 templates, JWT auth middleware, and all route handlers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from core.audit import _client_ip, log_login
from core.rate_limit import LoginGuard
from web.auth import AuthManager, ROLE_ADMIN

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT authentication middleware — protects all routes except /login and /health."""

    def __init__(self, app, auth_manager: AuthManager):
        super().__init__(app)
        self.auth = auth_manager
        self.public_paths = {"/login", "/health", "/favicon.ico"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths and static files. /api/ routes still authenticate
        # because we read the cookie below — but we don't redirect API callers
        # to /login; we let the route's own role guards return 401/403 JSON.
        is_api = path.startswith("/api/")
        if path in self.public_paths or path.startswith("/static"):
            return await call_next(request)

        token = request.cookies.get("access_token")
        user = self.auth.verify_token(token) if token else None
        if not user:
            if is_api:
                return JSONResponse({"error": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=302)

        # Make the authenticated user available to routes and templates.
        request.state.user = user
        request.state.username = user["username"]
        return await call_next(request)


def _resolve_login_user(username: str, password: str, config_manager, store, auth_manager):
    """Return {username, role} if creds valid, else None.

    Single source of truth: the bootstrap admin lives in config.yaml, every
    other user lives in the users table. No fall-through between them — the
    bootstrap user always authenticates against config (so a stale DB row
    cannot resurrect an old password).
    """
    if config_manager:
        cfg = config_manager.config.auth
        if username == cfg.default_username:
            if auth_manager.verify_password(password, cfg.default_password_hash):
                return {"username": username, "role": ROLE_ADMIN}
            return None
    return None  # placeholder; filled in by caller (see login_submit)


def create_app(
    config_manager=None,
    store=None,
    bus=None,
    cloud_connector=None,
    watchdog=None,
    auth_manager: Optional[AuthManager] = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Industrial Edge Server",
        description="Edge data collection and forwarding service",
        version="1.0.0",
        docs_url="/docs" if (config_manager and config_manager.config.server.debug) else None,
    )

    # Templates — inject `current_user` automatically into every render.
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def _ctx(request: Request) -> dict:
        return {"current_user": getattr(request.state, "user", None)}

    templates.env.globals["current_user_from"] = _ctx  # not required, but available

    # Static files
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Auth middleware
    if auth_manager:
        app.add_middleware(AuthMiddleware, auth_manager=auth_manager)

    # Login rate limiter (in-memory, process-local)
    auth_cfg = config_manager.config.auth if config_manager else None
    login_guard = LoginGuard(
        max_attempts=getattr(auth_cfg, "login_max_attempts", 5) if auth_cfg else 5,
        window_seconds=getattr(auth_cfg, "login_window_seconds", 300) if auth_cfg else 300,
        lockout_seconds=getattr(auth_cfg, "login_lockout_seconds", 300) if auth_cfg else 300,
    )
    app.state.login_guard = login_guard

    # ── Make `current_user` available in every TemplateResponse ─────────
    # We wrap TemplateResponse via a context processor pattern: each route
    # passes `request` in its context dict; we patch the dict here.
    _orig_response = templates.TemplateResponse

    def _patched_template_response(name, context, *args, **kwargs):
        request = context.get("request")
        if request is not None and "current_user" not in context:
            context["current_user"] = getattr(request.state, "user", None)
        return _orig_response(name, context, *args, **kwargs)

    templates.TemplateResponse = _patched_template_response  # type: ignore[assignment]

    # ── Inject dependencies into route modules ─────────
    from web.routes import adapters as adapters_routes
    from web.routes import dashboard as dashboard_routes
    from web.routes import settings as settings_routes
    from web.routes import activity as activity_routes
    from web.routes import snapshots as snapshots_routes
    from web.routes import users as users_routes
    from web.routes import audit as audit_routes

    dashboard_routes.templates = templates
    dashboard_routes.store = store
    dashboard_routes.bus = bus
    dashboard_routes.cloud_connector = cloud_connector
    dashboard_routes.watchdog = watchdog

    adapters_routes.templates = templates
    adapters_routes.store = store
    adapters_routes.config_manager = config_manager
    adapters_routes.http_connector = cloud_connector.http if cloud_connector else None

    settings_routes.templates = templates
    settings_routes.config_manager = config_manager
    settings_routes.cloud_connector = cloud_connector
    settings_routes.auth_manager = auth_manager
    settings_routes.store = store

    activity_routes.templates = templates
    activity_routes.store = store
    activity_routes.cloud_connector = cloud_connector

    snapshots_routes.templates = templates
    snapshots_routes.store = store
    snapshots_routes.config_manager = config_manager
    snapshots_routes.watchdog = watchdog
    snapshots_routes.http_connector = cloud_connector.http if cloud_connector else None

    users_routes.templates = templates
    users_routes.store = store
    users_routes.auth_manager = auth_manager
    users_routes.config_manager = config_manager

    audit_routes.templates = templates
    audit_routes.store = store

    # ── Register routes ────────────────────────────────
    app.include_router(dashboard_routes.router)
    app.include_router(adapters_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(activity_routes.router)
    app.include_router(snapshots_routes.router)
    app.include_router(users_routes.router)
    app.include_router(audit_routes.router)

    # ── Root + Login routes ────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return RedirectResponse("/dashboard")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": request.query_params.get("error", ""),
        })

    @app.post("/login")
    async def login_submit(request: Request):
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")
        ip = _client_ip(request)

        if not auth_manager:
            return RedirectResponse("/dashboard", status_code=302)

        # ── Rate-limit gate ────────────────────────────
        # Empty username → still rate-limited by IP so a bot can't probe blindly.
        locked, secs = login_guard.is_locked(username or "", ip)
        if locked:
            await log_login(store, request, username, success=False,
                            reason=f"locked_out:{secs}s")
            mins = max(1, (secs + 59) // 60)
            return RedirectResponse(
                f"/login?error=Too+many+attempts.+Try+again+in+{mins}+minute(s).",
                status_code=302,
            )

        # 1) Bootstrap admin in config.yaml (always authoritative for that name)
        if config_manager:
            cfg = config_manager.config.auth
            if username == cfg.default_username:
                if auth_manager.verify_password(password, cfg.default_password_hash):
                    login_guard.record_success(username, ip)
                    await log_login(store, request, username, success=True, role=ROLE_ADMIN)
                    token = auth_manager.create_token(username, ROLE_ADMIN)
                    response = RedirectResponse("/dashboard", status_code=302)
                    response.set_cookie(
                        "access_token", token,
                        httponly=True, max_age=cfg.jwt_expiry_minutes * 60
                    )
                    return response
                # Wrong password for the bootstrap admin — record + maybe lock.
                now_locked, lock_secs = login_guard.record_failure(username, ip)
                await log_login(store, request, username, success=False,
                                reason="bad_password" + (f" (locked {lock_secs}s)" if now_locked else ""))
                if now_locked:
                    mins = max(1, (lock_secs + 59) // 60)
                    return RedirectResponse(
                        f"/login?error=Account+locked+due+to+repeated+failures.+Try+again+in+{mins}+minute(s).",
                        status_code=302,
                    )
                return RedirectResponse("/login?error=Invalid+credentials", status_code=302)

        # 2) Other users live in DB with their own role
        if store:
            user = await store.get_user(username)
            if user and auth_manager.verify_password(password, user["password_hash"]):
                role = user.get("role") or ROLE_ADMIN
                login_guard.record_success(username, ip)
                await log_login(store, request, username, success=True, role=role)
                token = auth_manager.create_token(username, role)
                response = RedirectResponse("/dashboard", status_code=302)
                response.set_cookie("access_token", token, httponly=True, max_age=3600)
                return response

        # Generic failure — bad creds, unknown user, etc.
        now_locked, lock_secs = login_guard.record_failure(username or "", ip)
        await log_login(store, request, username, success=False,
                        reason="bad_password_or_unknown_user" + (f" (locked {lock_secs}s)" if now_locked else ""))
        if now_locked:
            mins = max(1, (lock_secs + 59) // 60)
            return RedirectResponse(
                f"/login?error=Account+locked+due+to+repeated+failures.+Try+again+in+{mins}+minute(s).",
                status_code=302,
            )
        return RedirectResponse("/login?error=Invalid+credentials", status_code=302)

    @app.get("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie("access_token")
        return response

    return app
