"""
FastAPI Application Factory — creates and configures the web server.

Sets up Jinja2 templates, JWT auth middleware, and all route handlers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from web.auth import AuthManager

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

        # Allow public paths and static files
        if path in self.public_paths or path.startswith("/static") or path.startswith("/api/"):
            return await call_next(request)

        # Check JWT cookie
        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse("/login", status_code=302)

        username = self.auth.verify_token(token)
        if not username:
            return RedirectResponse("/login", status_code=302)

        # Set user on request state
        request.state.username = username
        return await call_next(request)


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

    # Templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Static files
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Auth middleware
    if auth_manager:
        app.add_middleware(AuthMiddleware, auth_manager=auth_manager)

    # ── Inject dependencies into route modules ─────────
    from web.routes import adapters as adapters_routes
    from web.routes import dashboard as dashboard_routes
    from web.routes import settings as settings_routes
    from web.routes import activity as activity_routes
    from web.routes import snapshots as snapshots_routes

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

    activity_routes.templates = templates
    activity_routes.store = store
    activity_routes.cloud_connector = cloud_connector

    snapshots_routes.templates = templates
    snapshots_routes.store = store
    snapshots_routes.config_manager = config_manager
    snapshots_routes.watchdog = watchdog
    snapshots_routes.http_connector = cloud_connector.http if cloud_connector else None

    # ── Register routes ────────────────────────────────
    app.include_router(dashboard_routes.router)
    app.include_router(adapters_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(activity_routes.router)
    app.include_router(snapshots_routes.router)

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

        if not auth_manager:
            return RedirectResponse("/dashboard", status_code=302)

        # Check default user (config is single source of truth — never fall through to store)
        if config_manager:
            cfg = config_manager.config.auth
            if username == cfg.default_username:
                if auth_manager.verify_password(password, cfg.default_password_hash):
                    token = auth_manager.create_token(username)
                    response = RedirectResponse("/dashboard", status_code=302)
                    response.set_cookie(
                        "access_token", token,
                        httponly=True, max_age=cfg.jwt_expiry_minutes * 60
                    )
                    return response
                # Wrong password for the default user — reject immediately, don't check store
                return RedirectResponse("/login?error=Invalid+credentials", status_code=302)

        # Check DB users (for non-default users only)
        if store:
            user = await store.get_user(username)
            if user and auth_manager.verify_password(password, user["password_hash"]):
                token = auth_manager.create_token(username)
                response = RedirectResponse("/dashboard", status_code=302)
                response.set_cookie("access_token", token, httponly=True, max_age=3600)
                return response

        return RedirectResponse("/login?error=Invalid+credentials", status_code=302)

    @app.get("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie("access_token")
        return response

    return app
