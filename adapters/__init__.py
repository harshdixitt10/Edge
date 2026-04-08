# Protocol adapters package — plugin-based discovery.
# Each subfolder with a get_adapter_info() in its __init__.py is auto-registered.

from adapters.registry import get_registry, get_adapter_class, get_config_model, get_available_adapters  # noqa: F401
