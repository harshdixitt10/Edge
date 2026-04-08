"""
Adapter Plugin Registry — auto-discovers adapter folders at startup.

Each subfolder of adapters/ that contains an __init__.py with a
get_adapter_info() function is treated as a plugin.
Drop a folder in → adapter becomes available.  Remove it → gone.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, dict] = {}
_DISCOVERED = False


def discover_adapters() -> None:
    """Scan adapters/ for plugin folders and register them."""
    global _DISCOVERED
    adapters_dir = Path(__file__).parent

    for item in sorted(adapters_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("_"):
            continue
        if not (item / "__init__.py").exists():
            continue
        try:
            mod = importlib.import_module(f"adapters.{item.name}")
            if hasattr(mod, "get_adapter_info"):
                info = mod.get_adapter_info()
                _REGISTRY[info["type"]] = info
                logger.info(f"Loaded adapter plugin: {info['name']} ({info['type']})")
        except Exception as e:
            logger.warning(f"Failed to load adapter plugin '{item.name}': {e}")

    _DISCOVERED = True


def get_registry() -> dict[str, dict]:
    """Return the full registry dict  {adapter_type: info_dict}."""
    if not _DISCOVERED:
        discover_adapters()
    return _REGISTRY


def get_adapter_class(adapter_type: str):
    """Look up the adapter class for a given type string."""
    info = get_registry().get(adapter_type)
    return info["adapter_class"] if info else None


def get_config_model(adapter_type: str):
    """Look up the Pydantic config model for a given type string."""
    info = get_registry().get(adapter_type)
    return info["config_model"] if info else None


def get_available_adapters() -> list[dict]:
    """Return a list of info dicts for all discovered adapters."""
    return list(get_registry().values())
