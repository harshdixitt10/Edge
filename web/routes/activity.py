"""
Activity panel routes — shows enabled things with timestamps and sync status.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
logger = logging.getLogger(__name__)

# Will be set by app factory
templates: Jinja2Templates = None
store = None
cloud_connector = None


@router.get("/activity", response_class=HTMLResponse)
async def activity_panel(request: Request):
    """Render the activity panel page."""
    activities = await store.get_activities() if store else []
    adapters = await store.get_adapters() if store else []
    cloud_connected = cloud_connector.connected if cloud_connector else False

    # Build adapter → thing mapping for enrichment
    adapter_map = {}
    for a in adapters:
        if a["enabled"]:
            try:
                config = json.loads(a["config_json"])
                for thing in config.get("thing_configs", []):
                    tk = thing.get("thing_key", "")
                    if tk:
                        # Count metrics
                        metrics_count = len(thing.get("metric_mappings", []))
                        adapter_map[tk] = {
                            "adapter_name": a["name"],
                            "adapter_id": a["id"],
                            "thing_name": thing.get("name", ""),
                            "metrics_count": metrics_count,
                            "status": a["status"],
                        }
            except (json.JSONDecodeError, KeyError):
                pass

    # Build combined list — merge adapter config info with activity log
    combined = []
    seen_keys = set()

    for act in activities:
        seen_keys.add(act["thing_key"])
        info = adapter_map.get(act["thing_key"], {})
        combined.append({
            **act,
            "thing_name": info.get("thing_name", act.get("thing_name", "")),
            "adapter_name": info.get("adapter_name", act.get("adapter_name", "")),
            "metrics_count": info.get("metrics_count", act.get("metrics_count", 0)),
            "adapter_status": info.get("status", act.get("status", "unknown")),
        })

    # Add things from adapters that don't have activity logs yet
    for tk, info in adapter_map.items():
        if tk not in seen_keys:
            combined.append({
                "thing_key": tk,
                "thing_name": info["thing_name"],
                "adapter_name": info["adapter_name"],
                "adapter_id": info["adapter_id"],
                "status": info["status"],
                "adapter_status": info["status"],
                "metrics_count": info["metrics_count"],
                "last_event_ts": None,
                "last_ack_event_ts": None,
                "last_scan_ts": None,
                "last_ack_scan_ts": None,
                "last_alert_ts": None,
                "last_ack_alert_ts": None,
                "last_registered_error": None,
                "last_event_error": None,
                "last_scan_error": None,
                "last_alert_error": None,
                "events_sent": 0,
                "events_pending": 0,
            })

    return templates.TemplateResponse("activity.html", {
        "request": request,
        "activities": combined,
        "cloud_connected": cloud_connected,
        "total_things": len(combined),
    })


@router.get("/api/activity/status")
async def activity_status():
    """API endpoint for live activity panel updates."""
    activities = await store.get_activities() if store else []
    cloud_connected = cloud_connector.connected if cloud_connector else False
    return JSONResponse({
        "activities": activities,
        "cloud_connected": cloud_connected,
    })
