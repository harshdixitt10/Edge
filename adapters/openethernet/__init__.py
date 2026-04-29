"""OpenEthernet (TCP socket / Telnet-style) adapter plugin."""


def get_adapter_info():
    from adapters.openethernet.adapter import OpenethernetAdapter
    from adapters.openethernet.models import OpenethernetAdapterConfig

    return {
        "type": "openethernet",
        "name": "OpenEthernet",
        "description": (
            "Poll machine controllers over a raw TCP socket. Each tag is a "
            "command sent to the device; responses are framed by configurable "
            "ASCII prefix/suffix byte sequences."
        ),
        "adapter_class": OpenethernetAdapter,
        "config_model": OpenethernetAdapterConfig,
        "template": "openethernet_config.html",
        "icon_color": "var(--accent-amber)",
    }
