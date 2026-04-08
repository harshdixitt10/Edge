"""CSV adapter configuration models."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from core.models import MetricMapping


class CsvTagConfig(BaseModel):
    """Maps a CSV column to an edge-server tag."""
    tag_id: str
    column_name: str
    is_file_path: bool = False
    value_type: str = "number"


class CsvThingConfig(BaseModel):
    """Configuration for one logical device fed by CSV files."""
    thing_key: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    name: str = ""
    description: str = ""
    directory_url: str = ""
    file_filter: str = "*.csv"
    delimiter: str = ","
    has_header: bool = True
    scan_interval_ms: int = 5000
    send_interval_ms: int = 30000
    monitor_file_updates: bool = True
    timestamp_column: str = ""
    tag_configs: list[CsvTagConfig] = []
    metric_mappings: list[MetricMapping] = []
    disabled: bool = False


class CsvAdapterConfig(BaseModel):
    """Top-level configuration for the CSV adapter."""
    adapter_type: str = "csv"
    things: list[CsvThingConfig] = []
