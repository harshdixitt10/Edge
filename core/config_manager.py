"""
Configuration Manager — loads, validates, and saves config.yaml.

Uses Pydantic for validation and PyYAML for persistence.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import yaml

from core.models import AppConfig

logger = logging.getLogger(__name__)

# Resolve paths relative to the edge_server/ directory
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"


class ConfigManager:
    """Singleton-style config manager for the edge server."""

    def __init__(self, config_path: Optional[str | Path] = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._config: Optional[AppConfig] = None

    def load(self) -> AppConfig:
        """Load and validate configuration from YAML file."""
        if not self.config_path.exists():
            logger.warning(f"Config file not found at {self.config_path}, using defaults")
            self._config = AppConfig()
            self.save()
            return self._config

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            self._config = AppConfig(**raw)
            logger.info(f"Configuration loaded from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}. Using defaults.")
            self._config = AppConfig()

        # Ensure data and log directories exist
        data_dir = BASE_DIR / Path(self._config.database.path).parent
        log_dir = BASE_DIR / Path(self._config.logging.file).parent
        data_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)

        return self._config

    def save(self, config: Optional[AppConfig] = None) -> None:
        """Save configuration to YAML file."""
        if config:
            self._config = config

        if not self._config:
            raise ValueError("No configuration to save")

        data = self._config.model_dump()
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Configuration saved to {self.config_path}")

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            self.load()
        return self._config

    def update_cloud(self, **kwargs) -> AppConfig:
        """Update cloud configuration fields."""
        cloud_data = self.config.cloud.model_dump()
        cloud_data.update(kwargs)
        from core.models import CloudConfig
        self._config.cloud = CloudConfig(**cloud_data)
        self.save()
        return self._config

    def update_server(self, **kwargs) -> AppConfig:
        """Update server configuration fields."""
        server_data = self.config.server.model_dump()
        server_data.update(kwargs)
        from core.models import ServerConfig
        self._config.server = ServerConfig(**server_data)
        self.save()
        return self._config

    @contextmanager
    def update_config(self):
        """Context manager that yields the config for in-place editing and auto-saves on exit.

        Usage:
            with config_manager.update_config() as config:
                config.cloud.api_key = 'new-key'
            # auto-saved on exit
        """
        yield self.config
        self.save()

