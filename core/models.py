"""
Pydantic & SQLAlchemy models for the Industrial Edge Server.

Includes:
  - Data event models (DataEvent)
  - OPC-UA adapter configuration models (full hierarchy)
  - Application config models
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Data Event Model
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
# OPC-UA Adapter Configuration Models
# ─────────────────────────────────────────────

class TagSuffix(BaseModel):
    suffix_id: str
    expression_js: str


class ProtocolConnection(BaseModel):
    connection_id: str = Field(default_factory=lambda: f"connection_{uuid.uuid4().hex[:6]}")
    server_url: str = "opc.tcp://localhost:4840"
    discover_endpoints: bool = True
    application_uri: str = "urn:aliot:opcua:adapter"
    security_policy_uri: str = "http://opcfoundation.org/UA/SecurityPolicy#None"
    cert_alias: str = "opcua"
    auth_username: str = ""
    auth_password: str = ""
    auth_mechanism: str = "anonymous"  # anonymous | username | certificate
    auth_cert_alias: str = "opcua"
    timeout_millis: int = 10000


class ReadTag(BaseModel):
    tag_id: str
    node_id: str
    namespace: int = 2


class WriteTag(BaseModel):
    tag_id: str
    node_id: str
    namespace: int = 2


class SourceTag(BaseModel):
    protocol_connection: ProtocolConnection = Field(default_factory=ProtocolConnection)
    read_tags: list[ReadTag] = []
    write_tags: list[WriteTag] = []


class DerivedTag(BaseModel):
    """A derived tag computed from source tags using a JavaScript-like expression."""
    tag_id: str
    expression_js: str  # e.g., "(tag1 + tag2) / 2"
    source_tag_ids: list[str] = []  # tag_ids referenced in expression
    type: str = "number"  # number | string | boolean


class MetricMapping(BaseModel):
    metric_id: str
    tag_id: str
    type: str = "number"  # number | string | boolean


class ThingConfig(BaseModel):
    thing_key: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = ""
    description: str = ""
    bi_directional: bool = False
    config_mode: str = "read_write"
    send_interval_ms: int = 30000
    scan_interval_ms: int = 2000
    adapter_reset_interval: int = -1
    heartbeat_interval_ms: int = 120000
    alert_interval_ms: int = 900000
    alert_messages_monitoring_time_window: int = 900000
    alert_messages_threshold: int = 0
    alert_message_count: int = 3
    publish_mode: str = "server"  # server | poll
    source_tags: list[SourceTag] = []
    ml_tags: list = []
    slot_tags: list = []
    derived_tags: list[DerivedTag] = []
    eval_order_tag_ids: list[str] = []
    monitor_tag_ids: list[str] = []
    scan_tag_ids: list[str] = []
    datetime_tag_id: str = ""
    monitor_expression_js: str = ""
    local_util_expression_js: str = ""
    metric_mappings: list[MetricMapping] = []
    disabled: bool = False
    include_prev_tags_to_scan_message: bool = True


class OpcuaAdapterConfig(BaseModel):
    adapter_type: str = "com.abc.gateway.edge.opcuaadapter.OpcuaAdapter"
    auto_concurrency: bool = True
    threadpool_size: int = 1
    schedule_delay_mills: int = 200
    test_timeout_secs: int = 20
    detect_nashorn_leak: bool = True
    global_util_expression_js: str = ""
    tag_suffixes: list[TagSuffix] = []
    thing_configs: list[ThingConfig] = []


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
    path: str = "data/edge_server.db"
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
