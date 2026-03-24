# UI Changes

## Summary
All templates updated to remove emoji icons and add professional UI components.

## Changes Made

### base.html (Complete Rewrite)
- **Light Theme**: Added `[data-theme="light"]` CSS variables for full light mode support
- **Theme Toggle**: Added toggle switch in sidebar footer with `localStorage` persistence
- **SVG Icons**: Replaced all emoji sidebar icons with clean inline SVG icons
- **Logo**: Changed from emoji to "ES" text block with gradient background

### opcua_config.html (Major Overhaul)
- **Thing Summary Table**: Added 13-column summary table at top of Section B
  - Columns: Thing Name, Thing Key, Scan Interval, Send Interval, Alert Interval, Connections, Read Tags, Derived Tags, Write Tags, Total Tags, Metrics, Delete, Enable/Disable
  - Click any row to scroll to the thing's detailed config
  - Auto-refreshes when tags/things are added/removed
- **Search Bar**: Filter things by name or key; highlights matching thing card with blue border
- **Derived Tags Section**: New per-thing table with columns: Tag ID, Expression (JS), Source Tag IDs (comma-separated), Type (number/string/boolean)
  - Wired into `buildConfig()` JS function for JSON serialization
- **Emoji Removal**: All emoji icons replaced with text labels

### dashboard.html
- Replaced metric card emojis (CPU, MEM, UP, DSK) with styled text abbreviations
- Removed emojis from card titles (Cloud Connection, Buffer Status, Adapters, Watchdog, Live Data Feed)
- Updated title bar to use "Online/Offline" instead of colored circles

### settings.html (Rewrite)
- Removed all emojis from section headers
- Added smart key handling: hides placeholder values ("access key" / "secret key") in input fields
- Clean system info grid layout

### activity.html
- Removed emojis from page title, cloud status badge, empty state icon, and CTA button

### adapters.html
- Removed type-specific emojis from adapter cards
- Cleaned all button labels (Edit, Stop, Start, Delete)
- Clean empty state without icons

### adapter_select.html
- Replaced protocol emojis with styled text labels (OPC-UA, Modbus, MQTT)

### login.html
- Replaced lightning bolt emoji with "ES" text logo
