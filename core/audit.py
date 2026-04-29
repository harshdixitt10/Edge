"""Audit logging helpers.

Used by route handlers to record privileged actions: who did what, when, from
where. Pairs with RBAC — RBAC says who *can* act, audit says who *did*.

Design notes:
  - Failures must NEVER crash the calling request. write_audit() in the store
    swallows DB errors and logs a warning.
  - We extract user/IP/UA from the FastAPI Request so callers don't repeat
    the boilerplate.
  - Pass `store` explicitly because route modules each get their own injected
    `store` reference at app-factory time; this helper has no global handle.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import Request

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    # X-Forwarded-For wins if present (the box may sit behind a reverse proxy)
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host or ""
    return ""


def _user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:200]


def _user_from_request(request: Request) -> tuple[str, str]:
    user = getattr(request.state, "user", None) or {}
    return user.get("username", ""), user.get("role", "")


async def log_action(
    store,
    request: Request,
    action: str,
    *,
    resource_type: str = "",
    resource_id: str = "",
    details: Any = None,
    result: str = "success",
    username: Optional[str] = None,
    role: Optional[str] = None,
) -> None:
    """Record a privileged action. Safe to call without awaiting in tight paths.

    `details` may be a dict / list / str — we JSON-serialize dict/list, store
    str as-is. Use it for things like the new value of a setting, or a list of
    affected IDs.
    """
    if store is None:
        return
    if username is None or role is None:
        u, r = _user_from_request(request)
        username = username if username is not None else u
        role = role if role is not None else r

    if isinstance(details, (dict, list)):
        try:
            details_str = json.dumps(details, default=str)
        except Exception:
            details_str = str(details)
    elif details is None:
        details_str = ""
    else:
        details_str = str(details)

    await store.write_audit(
        username=username,
        role=role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
        details=details_str,
        result=result,
    )


async def log_login(
    store,
    request: Request,
    username: str,
    *,
    success: bool,
    role: str = "",
    reason: str = "",
) -> None:
    """Convenience wrapper for /login attempts."""
    await log_action(
        store,
        request,
        action="login",
        resource_type="user",
        resource_id=username,
        details=reason or ("ok" if success else "invalid_credentials"),
        result="success" if success else "failure",
        username=username,
        role=role,
    )
