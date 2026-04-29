#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  Datonis Edge Server — Install & Run as systemd Service
#  Usage:  sudo bash install.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SERVICE_NAME="datonis-edge-server"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_DIR="$SCRIPT_DIR"
VENV_DIR="$EDGE_DIR/venv"
PYTHON="$(command -v python3 || command -v python)"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$(whoami)}"

# ── Colors ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo "════════════════════════════════════════════════════════"
echo "   Datonis Edge Server — Installer (Linux)"
echo "════════════════════════════════════════════════════════"
echo ""

# ── Pre-checks ──
if [[ $EUID -ne 0 ]]; then
    err "This script must be run with sudo / as root."
    exit 1
fi

if [[ -z "$PYTHON" ]]; then
    err "Python 3 not found. Install it first: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

info "Python found: $PYTHON ($($PYTHON --version 2>&1))"
info "Edge server directory: $EDGE_DIR"
info "Service will run as user: $RUN_USER"
echo ""

# ── Step 1: Stop existing service if running ──
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    warn "Existing service is running — stopping it first..."
    systemctl stop "$SERVICE_NAME"
    ok "Service stopped"
fi

# ── Step 2: Fresh configuration ──
info "Ensuring fresh configuration..."

# Reset config.yaml to template (fresh install = no leftover config)
if [[ -f "$EDGE_DIR/config.template.yaml" ]]; then
    cp "$EDGE_DIR/config.template.yaml" "$EDGE_DIR/config.yaml"
    ok "config.yaml reset to template (fresh)"
else
    warn "config.template.yaml not found — keeping existing config.yaml"
fi

# Remove old database so the server starts clean
PARENT_DIR="$(dirname "$EDGE_DIR")"
if [[ -f "$PARENT_DIR/data/edge_server.db" ]]; then
    rm -f "$PARENT_DIR/data/edge_server.db"
    ok "Old database removed (fresh start)"
fi

# ── Step 3: Create virtual environment ──
info "Setting up Python virtual environment..."
$PYTHON -m venv "$VENV_DIR"
ok "Virtual environment created at $VENV_DIR"

# ── Step 4: Install dependencies ──
info "Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$EDGE_DIR/requirements.txt" --quiet
ok "Dependencies installed"

# ── Step 5: Create required directories ──
mkdir -p "$PARENT_DIR/logs"
mkdir -p "$PARENT_DIR/data"
mkdir -p "$PARENT_DIR/Snapshot Backup"
mkdir -p "$PARENT_DIR/Configuration Backup/opcua_conf_backup"
mkdir -p "$PARENT_DIR/Configuration Backup/csv_conf_backup"
mkdir -p "$PARENT_DIR/Configuration Backup/mtconnect_conf_backup"
mkdir -p "$PARENT_DIR/Configuration Backup/openethernet_conf_backup"
ok "Directories created (logs, data, Snapshot Backup, Configuration Backup)"

# ── Step 6: Fix ownership ──
chown -R "$RUN_USER":"$RUN_USER" "$EDGE_DIR"
chown -R "$RUN_USER":"$RUN_USER" "$PARENT_DIR/logs"
chown -R "$RUN_USER":"$RUN_USER" "$PARENT_DIR/data"
chown -R "$RUN_USER":"$RUN_USER" "$PARENT_DIR/Snapshot Backup"
chown -R "$RUN_USER":"$RUN_USER" "$PARENT_DIR/Configuration Backup"
ok "File ownership set to $RUN_USER"

# ── Step 7: Create systemd service unit ──
info "Creating systemd service..."

cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=Datonis Edge Server
After=network.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$EDGE_DIR
ExecStart=$VENV_DIR/bin/python main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

ok "Service file created: $SERVICE_FILE"

# ── Step 8: Enable and start ──
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"
ok "Service enabled and started"

echo ""
echo "════════════════════════════════════════════════════════"
echo "   Installation complete!"
echo ""
echo "   Service name:  $SERVICE_NAME"
echo "   Web UI:        http://localhost:8090"
echo "   Login:         admin / changeme"
echo ""
echo "   Useful commands:"
echo "     sudo systemctl status  $SERVICE_NAME"
echo "     sudo systemctl stop    $SERVICE_NAME"
echo "     sudo systemctl restart $SERVICE_NAME"
echo "     sudo journalctl -u $SERVICE_NAME -f"
echo "════════════════════════════════════════════════════════"
echo ""
