#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  Datonis Edge Server — Uninstall Service
#  Usage:  sudo bash uninstall.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SERVICE_NAME="datonis-edge-server"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_DIR="$SCRIPT_DIR"
VENV_DIR="$EDGE_DIR/venv"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ── Colors ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }

echo ""
echo "════════════════════════════════════════════════════════"
echo "   Datonis Edge Server — Uninstaller (Linux)"
echo "════════════════════════════════════════════════════════"
echo ""

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}[ERROR]${NC} This script must be run with sudo / as root."
    exit 1
fi

# ── Step 1: Stop service ──
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    info "Stopping service..."
    systemctl stop "$SERVICE_NAME"
    ok "Service stopped"
else
    info "Service is not running"
fi

# ── Step 2: Disable and remove service unit ──
if [[ -f "$SERVICE_FILE" ]]; then
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    ok "Service unit removed"
else
    info "Service file not found — nothing to remove"
fi

# ── Step 3: Remove virtual environment ──
if [[ -d "$VENV_DIR" ]]; then
    rm -rf "$VENV_DIR"
    ok "Virtual environment removed"
else
    info "No virtual environment found"
fi

echo ""
echo "════════════════════════════════════════════════════════"
echo "   Uninstall complete!"
echo ""
echo "   The service has been stopped and removed."
echo "   Your config, data, logs, and backups are preserved."
echo ""
echo "   To remove all data:  rm -rf \"$(dirname "$EDGE_DIR")/{data,logs}\""
echo "════════════════════════════════════════════════════════"
echo ""
