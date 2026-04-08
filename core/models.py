"""
Shared Pydantic models for the Industrial Edge Server.

Adapter-specific models live in their plugin folders:
  adapters/opcua/models.py, adapters/csv/models.py, etc.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Data Event Model (used by all adapters)
# ─────────────────────────────────────────────

class DataEvent(BaseModel):
    """A single data event captured from an adapter."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    adapter_name: str
    thing_key: str = ""
    node_id: str
    namespace: int = 0
    tag_id: str = ""
    metric_id: str = ""
    value: float | int | str | bool
    quality: str = "Good"  # Good / Bad / Uncertain
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent: bool = False
    is_backfill: bool = False


# ─────────────────────────────────────────────
# Shared Adapter Config Models
# ─────────────────────────────────────────────

class MetricMapping(BaseModel):
    metric_id: str
    tag_id: str
    type: str = "number"  # number | string | boolean


# ─────────────────────────────────────────────
# Application Configuration Models
# ─────────────────────────────────────────────

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    secret_key: str = "change-me-in-production"
    debug: bool = False


class AuthConfig(BaseModel):
    default_username: str = "admin"
    default_password_hash: str = ""
    jwt_expiry_minutes: int = 60
    jwt_algorithm: str = "HS256"


class CloudConfig(BaseModel):
    protocol: str = "https"
    endpoint_url: str = "https://api.datonis.io:443"
    api_key: str = "access key"
    secret_key: str = "secret key"
    gateway_key: str = ""
    edge_id: str = "edge-plant-01"
    timeout_secs: int = 10
    batch_size: int = 100
    heartbeat_interval_secs: int = 60
    retry_on_status: list[int] = [500, 502, 503, 504]
    ssl_verify: bool = True


class DatabaseConfig(BaseModel):
    path: str = "../data/edge_server.db"
    retention_days: int = 7


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "../logs/edge_server.log"


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
