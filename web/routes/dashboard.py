"""
Dashboard routes — system status, live data feed, health endpoint.
"""

from __future__ import annotations

import platform
import time
from datetime import datetime, timezone

import psutil
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

# Will be set by app factory
templates: Jinja2Templates = None
store = None
bus = None
cloud_connector = None
watchdog = None
start_time = time.time()


def get_system_stats() -> dict:
    """Get system resource usage."""
    try:
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_used_mb": round(psutil.virtual_memory().used / (1024 * 1024)),
            "memory_total_mb": round(psutil.virtual_memory().total / (1024 * 1024)),
            "disk_percent": psutil.disk_usage("/").percent if platform.system() != "Windows" else psutil.disk_usage("C:\\").percent,
            "disk_used_gb": round(
                (psutil.disk_usage("/").used if platform.system() != "Windows" else psutil.disk_usage("C:\\").used) / (1024**3), 1
            ),
            "disk_total_gb": round(
                (psutil.disk_usage("/").total if platform.system() != "Windows" else psutil.disk_usage("C:\\").total) / (1024**3), 1
            ),
            "uptime_secs": int(time.time() - start_time),
            "platform": platform.system(),
            "python_version": platform.python_version(),
        }
    except Exception:
        return {
            "cpu_percent": 0, "memory_percent": 0, "memory_used_mb": 0,
            "memory_total_mb": 0, "disk_percent": 0, "disk_used_gb": 0,
            "disk_total_gb": 0, "uptime_secs": int(time.time() - start_time),
            "platform": platform.system(), "python_version": platform.python_version(),
        }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the main dashboard page."""
    stats = get_system_stats()
    unsent_count = await store.get_unsent_count() if store else 0
    total_count = await store.get_total_count() if store else 0
    recent_events = await store.get_recent_events(limit=20) if store else []
    adapters_list = await store.get_adapters() if store else []
    cloud_connected = cloud_connector.connected if cloud_connector else False
    task_statuses = watchdog.get_task_statuses() if watchdog else {}

    # Format uptime
    uptime = stats["uptime_secs"]
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    cloud_status = "Connected" if cloud_connected else "Disconnected"
    if unsent_count > 0 and not cloud_connected:
        cloud_status = "Buffering"

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "uptime_str": uptime_str,
        "unsent_count": unsent_count,
        "total_count": total_count,
        "recent_events": recent_events,
        "adapters": adapters_list,
        "cloud_status": cloud_status,
        "cloud_connected": cloud_connected,
        "task_statuses": task_statuses,
        "pending_bus_events": bus.pending if bus else 0,
    })


@router.get("/health")
async def health_check():
    """Health check endpoint used by systemd WatchdogSec."""
    return JSONResponse({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


@router.get("/api/status")
async def api_status():
    """API endpoint for live dashboard updates."""
    stats = get_system_stats()
    unsent_count = await store.get_unsent_count() if store else 0
    total_count = await store.get_total_count() if store else 0
    recent_events = await store.get_recent_events(limit=20) if store else []
    adapters_list = await store.get_adapters() if store else []

    return JSONResponse({
        "system": stats,
        "cloud_connected": cloud_connector.connected if cloud_connector else False,
        "unsent_count": unsent_count,
        "total_count": total_count,
        "adapters": adapters_list,
        "recent_events": [
            {
                "id": e.id[:8],
                "adapter": e.adapter_name,
                "node_id": e.node_id,
                "tag_id": e.tag_id,
                "value": e.value,
                "quality": e.quality,
                "timestamp": e.timestamp.isoformat(),
                "sent": e.sent,
            }
            for e in recent_events
        ],
        "bus_pending": bus.pending if bus else 0,
    })
