"""MTConnect adapter plugin."""


def get_adapter_info():
    from adapters.mtconnect.adapter import MTConnectAdapter
    from adapters.mtconnect.models import MtConnectAdapterConfig

    return {
        "type": "mtconnect",
        "name": "MTConnect",
        "description": (
            "Poll MTConnect agent servers via HTTP and extract CNC/machine data "
            "using XPath expressions on the XML response."
        ),
        "adapter_class": MTConnectAdapter,
        "config_model": MtConnectAdapterConfig,
        "template": "mtconnect_config.html",
        "icon_color": "var(--accent-purple)",
    }
