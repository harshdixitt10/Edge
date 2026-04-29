"""OpenEthernet adapter configuration models.

Mirrors the Java reference (datonis_edge com.altizon.gateway.edge.openethernet):
  - Connection: ip, port, prefix sequence, suffix sequence, timeout
  - Tag: a single command string the device understands (e.g. "Q600 RPM")
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

from core.models import MetricMapping


class OpenethernetTagConfig(BaseModel):
    """Maps a device command to an edge-server tag."""
    tag_id: str
    command: str
    value_type: str = "number"  # number | string | boolean


class OpenethernetThingConfig(BaseModel):
    """Configuration for one logical device reachable via TCP socket."""
    thing_key: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = ""
    description: str = ""

    ip_address: str = "192.168.120.1"
    port: int = 4000

    prefix: str = "13,10,2"
    suffix: str = "23,13,10,62"

    scan_interval_ms: int = 5000
    send_interval_ms: int = 30000
    timeout_ms: int = 10000

    tag_configs: list[OpenethernetTagConfig] = []
    metric_mappings: list[MetricMapping] = []
    disabled: bool = False

    @field_validator("prefix", "suffix")
    @classmethod
    def _validate_byte_sequence(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prefix/suffix cannot be empty")
        for token in v.split(","):
            token = token.strip()
            if not token.isdigit():
                raise ValueError(f"non-numeric byte in sequence: '{token}'")
            n = int(token)
            if n < 0 or n > 255:
                raise ValueError(f"byte out of range 0-255: {n}")
        return v


class OpenethernetAdapterConfig(BaseModel):
    """Top-level configuration for the OpenEthernet adapter."""
    adapter_type: str = "openethernet"
    things: list[OpenethernetThingConfig] = []
