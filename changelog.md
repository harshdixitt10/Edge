# Edge Server Implementation Changes

## Logs Directory Relocation
- **Files Modified**: `config.yaml`, `core/models.py`
- **Changes**: 
  - Updated the default `LoggingConfig` path in `core/models.py` to `../logs/edge_server.log`.
  - Updated the existing `config.yaml` to change the `file` configuration to `../logs/edge_server.log`.
  - This effectively forces the server to store its log file horizontally adjacent to the `edge_server` folder.

## Timestamp Localizations
- **Files Modified**: `web/templates/dashboard.html`, `web/routes/activity.py`
- **Changes**: 
  - Discovered that the Live Data Feed in `dashboard.html` was printing raw UTC time. Automatically processed instances of `event.timestamp` using `.astimezone()` before formatting `strftime('%H:%M:%S')`.
  - Configured `activity.py` backend to parse SQLite UTC ISO strings into datetime objects, convert them explicitly to the edge server's system local timezone (`tzlocal()`), and inject them cleanly formatted for the Activity Panel. 

## Enhanced Activity Panel with Metrics UI
- **Files Modified**: `web/routes/activity.py`, `web/templates/activity.html`
- **Changes**: 
  - Updated the `/activity` endpoint to traverse all the `metric_mappings` and `derived_tags` for configured Adapters and output a detailed `metrics_list` mapping to the backend response loop. 
  - Configured the frontend HTML table rendering of the _Metrics_ count column to act as a Javascript anchor instead of plain text. 
  - Created a sleek dark-themed Modal Overlay `showMetricsModal()` to elegantly display detailed tabular metrics including `Tag Name`, `Data Type`, and `Tag Type` whenever the user clicks into an Activity Panel metrics cell. 
  - Paused the background auto-refresh Javascript loop if the modal overlay is intentionally being viewed by the user, providing a seamless operational experience.

## Optimized Live Network Data Delivery & Backfilling
- **Files Modified**: `cloud/connector.py`, `cloud/backfill.py`
- **Changes**: 
  - Restructured the CloudConnector's core `run_pipeline` architecture to entirely bypass the local SQLite buffering pipeline when the edge server experiences healthy network connection (**Case 1**). This natively removes millions of unnecessary Disk I/O ops for perfectly synchronous Data Events.
  - Re-engineered the `BackfillEngine` task pool polling. It now actively and continuously cycles over the LocalStore evaluating for `unsent_count > 0` thresholds rather than only triggering off a reconnect flag. This directly solves the issue where buffered queues would stay locked after a disruption until another distinct disconnection happened.
  - Now, if an offline network issue drops connection (**Case 2**), buffering immediately commences within SQLite. Once stability returns, the `BackfillEngine` seizes pipeline authority to aggressively backfill the queued packets strictly in chronological order to the Datonis Cloud while intelligently stalling the primary live feed until cleanly synchronized (**ToDo** objective met).
