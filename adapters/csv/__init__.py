"""CSV file-reader adapter plugin."""


def get_adapter_info():
    from adapters.csv.adapter import CSVAdapter
    from adapters.csv.models import CsvAdapterConfig

    return {
        "type": "csv",
        "name": "CSV",
        "description": (
            "Read data from CSV files in a directory. Supports column-to-tag mapping, "
            "file change monitoring, and configurable scan intervals."
        ),
        "adapter_class": CSVAdapter,
        "config_model": CsvAdapterConfig,
        "template": "csv_config.html",
        "icon_color": "var(--accent-green)",
    }
