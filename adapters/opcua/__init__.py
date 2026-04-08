"""OPC-UA adapter plugin."""


def get_adapter_info():
    from adapters.opcua.adapter import OPCUAAdapter, test_opcua_connection
    from adapters.opcua.models import OpcuaAdapterConfig

    return {
        "type": "opcua",
        "name": "OPC-UA",
        "description": (
            "Connect to OPC-UA servers for PLC, SCADA, and DCS data collection. "
            "Supports subscriptions, security policies, and namespace browsing."
        ),
        "adapter_class": OPCUAAdapter,
        "config_model": OpcuaAdapterConfig,
        "template": "opcua_config.html",
        "icon_color": "var(--accent-blue)",
        "test_connection": test_opcua_connection,
    }
