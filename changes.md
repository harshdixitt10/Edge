# Changes Log — Edge Server Fixes

**Date:** 2026-03-27

## 1. Fixed CSV Adapter Not Running

**File:** `adapters/base_adapter.py`

- **Root Cause:** `import asyncio` was placed inside an `except` block (line 74) instead of at the top of the file. This caused `NameError` on the first iteration of the reconnect loop, preventing adapters from starting.
- **Fix:** Moved `import asyncio` to top-level imports and removed the inline import.

## 2. Made OPCUA & CSV Adapters Independent

**File:** `main.py`

- Changed `run_adapters()` to lazy-load each adapter class with individual `try/except` blocks.
- If `asyncua` is not installed, only a warning is logged — the CSV adapter still works normally, and vice versa.
- Each adapter type can now run independently without throwing errors about the other.

## 3. Activity Panel — Show Only Current Things

**File:** `web/routes/activity.py`

- Previously, the activity panel merged all entries from the `activity_log` DB table, including stale entries from old adapter configurations.
- Added a filter: only thing_keys present in currently-enabled adapter configs are displayed.
- When an adapter is reconfigured with different things, old things no longer appear.

## 4. Fixed Toggle Switch CSS Alignment

**File:** `web/templates/base.html`

- Fixed `.toggle-slider::before` positioning: changed from `bottom: 2px` to `top: 50%; transform: translateY(-50%)` for proper vertical centering.
- Fixed checked state transform to include both vertical centering and horizontal slide: `transform: translateY(-50%) translateX(20px)`.

## 5. Added Sidebar Collapse (Hamburger Menu)

**File:** `web/templates/base.html`

- Added a hamburger (☰) toggle button positioned next to the sidebar.
- Clicking toggles a `.collapsed` class on the sidebar, sliding it off-screen.
- Main content area expands to full width when sidebar is collapsed.
- Sidebar state persisted in `localStorage` (key: `edge-sidebar-collapsed`).
- Smooth cubic-bezier transition animations on both sidebar and toggle button.
- Works correctly on mobile breakpoints.

## 6. Replaced Icons for Uniformity

**Files:** `web/templates/base.html`, `web/templates/adapters.html`, `web/templates/activity.html`

- **Theme toggle:** Replaced text "L" / "D" with proper sun ☀ and moon 🌙 SVG icons.
- **Sidebar footer:** Added user icon next to "Admin" and logout icon next to "Logout" link.
- **Theme label:** Added sun SVG icon next to "Theme" label.
- **Adapters page:** Added adapter-type icons (file icon for CSV, network icon for OPC-UA) on each card.
- **Adapter buttons:** Added SVG icons to "Add Adapter" (+), "Edit" (pencil), and "Delete" (trash) buttons.
- **Empty states:** Replaced plain text "No Adapters" / "No Data" with proper SVG icons (adapter icon, pulse/activity icon).
- All icons use consistent Lucide-style SVGs at 18×18 for nav, 14×14 for inline, and 48×48 for empty states.
