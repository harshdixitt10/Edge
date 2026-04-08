"""MTConnect adapter configuration models."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from core.models import MetricMapping


class MtConnectTagConfig(BaseModel):
    """Maps an XPath expression to an edge-server tag."""
    tag_id: str
    tag_path: str          # XPath into the MTConnect XML response
    value_type: str = "number"  # number | string | boolean


class MtConnectThingConfig(BaseModel):
    """Configuration for one logical device fed by an MTConnect agent."""
    thing_key: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = ""
    description: str = ""
    server_url: str = "http://localhost:5000/current"
    scan_interval_ms: int = 5000
    send_interval_ms: int = 30000
    timeout_secs: int = 10
    verify_ssl: bool = False
    tag_configs: list[MtConnectTagConfig] = []
    metric_mappings: list[MetricMapping] = []
    disabled: bool = False


class MtConnectAdapterConfig(BaseModel):
    """Top-level configuration for the MTConnect adapter."""
    adapter_type: str = "mtconnect"
    things: list[MtConnectThingConfig] = []
