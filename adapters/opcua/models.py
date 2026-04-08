"""OPC-UA adapter configuration models."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from core.models import MetricMapping


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
    expression_js: str
    source_tag_ids: list[str] = []
    type: str = "number"


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
    publish_mode: str = "server"
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
