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
                        # Collect metrics details
                        metrics_list = []
                        for tm in thing.get("metric_mappings", []):
                            metrics_list.append({
                                "tagname": tm.get("tag_id", ""),
                                "datatype": tm.get("type", "unknown"),
                                "tagtype": "Read Tag"
                            })
                        for dt in thing.get("derived_tags", []):
                            metrics_list.append({
                                "tagname": dt.get("tag_id", ""),
                                "datatype": dt.get("type", "unknown"),
                                "tagtype": "Derived Tag"
                            })
                            
                        metrics_count = len(thing.get("metric_mappings", []))
                        adapter_map[tk] = {
                            "adapter_name": a["name"],
                            "adapter_id": a["id"],
                            "thing_name": thing.get("name", ""),
                            "metrics_count": metrics_count,
                            "metrics_list": metrics_list,
                            "status": a["status"],
                        }
            except (json.JSONDecodeError, KeyError):
                pass

    def to_local_time(ts_val):
        """Convert a UTC timestamp (string or datetime) to formatted local string."""
        if not ts_val:
            return None
        try:
            if isinstance(ts_val, str):
                # Handle ISO 8601 strings from SQLite/Pydantic
                # Replace T with space for nicer display if needed, but for parsing:
                ts_fixed = ts_val.replace('Z', '+00:00')
                try:
                    dt = datetime.fromisoformat(ts_fixed)
                except ValueError:
                    # Fallback for formats from_isoformat might miss
                    ts_fixed = ts_fixed.replace('T', ' ').split('.')[0]
                    dt = datetime.strptime(ts_fixed, '%Y-%m-%d %H:%M:%S')
                
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = ts_val
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
            
            # astimezone() without args converts to system local time
            return dt.astimezone().strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"Error converting timestamp '{ts_val}' to local: {e}")
            return str(ts_val)

    # Build combined list — merge adapter config info with activity log
    combined = []
    seen_keys = set()

    for act in activities:
        seen_keys.add(act["thing_key"])
        info = adapter_map.get(act["thing_key"], {})
        
        # Convert timestamps to local time
        act["last_event_ts"] = to_local_time(act.get("last_event_ts"))
        act["last_ack_event_ts"] = to_local_time(act.get("last_ack_event_ts"))
        act["last_scan_ts"] = to_local_time(act.get("last_scan_ts"))
        act["last_ack_scan_ts"] = to_local_time(act.get("last_ack_scan_ts"))
        act["last_alert_ts"] = to_local_time(act.get("last_alert_ts"))
        act["last_ack_alert_ts"] = to_local_time(act.get("last_ack_alert_ts"))
        
        combined.append({
            **act,
            "thing_name": info.get("thing_name", act.get("thing_name", "")),
            "adapter_name": info.get("adapter_name", act.get("adapter_name", "")),
            "metrics_count": info.get("metrics_count", act.get("metrics_count", 0)),
            "metrics_list": info.get("metrics_list", []),
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
                "metrics_list": info["metrics_list"],
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
