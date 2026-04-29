"""Audit log viewer (admin-only)."""

from __future__ import annotations

import csv
import io
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from web.auth import require_role

router = APIRouter()
logger = logging.getLogger(__name__)

# Set by app factory
templates: Jinja2Templates = None
store = None


PAGE_SIZE = 100


@router.get("/audit", response_class=HTMLResponse, dependencies=[Depends(require_role("admin"))])
async def audit_page(request: Request):
    qp = request.query_params
    username_filter = qp.get("username") or None
    action_filter = qp.get("action") or None
    result_filter = qp.get("result") or None
    try:
        page = max(1, int(qp.get("page", "1")))
    except ValueError:
        page = 1
    offset = (page - 1) * PAGE_SIZE

    rows = await store.get_audit_log(
        username=username_filter,
        action=action_filter,
        result=result_filter,
        limit=PAGE_SIZE,
        offset=offset,
    )
    total = await store.count_audit_log(
        username=username_filter, action=action_filter, result=result_filter,
    )
    distinct_actions = await store.get_distinct_audit_actions()
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse("audit.html", {
        "request": request,
        "rows": rows,
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "distinct_actions": distinct_actions,
        "username_filter": username_filter or "",
        "action_filter": action_filter or "",
        "result_filter": result_filter or "",
    })


@router.get("/audit/export.csv", dependencies=[Depends(require_role("admin"))])
async def audit_export(request: Request):
    """Export the (filtered) audit log as CSV."""
    qp = request.query_params
    rows = await store.get_audit_log(
        username=qp.get("username") or None,
        action=qp.get("action") or None,
        result=qp.get("result") or None,
        limit=10_000,
        offset=0,
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "timestamp", "username", "role", "action",
        "resource_type", "resource_id", "ip_address",
        "user_agent", "details", "result",
    ])
    for r in rows:
        writer.writerow([r[c] for c in (
            "id", "timestamp", "username", "role", "action",
            "resource_type", "resource_id", "ip_address",
            "user_agent", "details", "result",
        )])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
