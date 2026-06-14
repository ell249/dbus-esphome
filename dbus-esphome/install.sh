#!/bin/bash
# install.sh  –  Install dbus-esphome on a Victron Venus OS device (Cerbo GX etc.)
#
# Run on the Cerbo GX via SSH:
#   bash /path/to/install.sh
#
# What it does:
#   1. Copies driver files to /data/dbus-esphome/  (survives firmware updates)
#   2. Installs aioesphomeapi and its dependencies into ./vendor/
#   3. Sets up a daemontools service symlink so the driver starts on boot
#   4. Registers a /data/rc.local hook so the service is re-linked after updates

set -e

INSTALL_DIR="/data/dbus-esphome"
SERVICE_DIR="/service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== dbus-esphome installer ==="

# ── 1. Copy files ────────────────────────────────────────────────────────────────
echo "[1/4] Copying driver files to $INSTALL_DIR …"
mkdir -p "$INSTALL_DIR"
cp -f "$SCRIPT_DIR/dbus-esphome.py"   "$INSTALL_DIR/"
cp -f "$SCRIPT_DIR/uninstall.sh"      "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/dbus-esphome.py"
chmod +x "$INSTALL_DIR/uninstall.sh"

# Copy config only if it doesn't already exist (preserve user edits)
if [ ! -f "$INSTALL_DIR/config.ini" ]; then
    cp -f "$SCRIPT_DIR/config.ini" "$INSTALL_DIR/"
    echo "    config.ini copied – edit $INSTALL_DIR/config.ini with your device IP(s)"
else
    echo "    config.ini already exists, skipping (keeping existing config)"
fi

# Service runner scripts
mkdir -p "$INSTALL_DIR/service/log"
cp -f "$SCRIPT_DIR/service/run"     "$INSTALL_DIR/service/"
cp -f "$SCRIPT_DIR/service/log/run" "$INSTALL_DIR/service/log/"
chmod +x "$INSTALL_DIR/service/run"
chmod +x "$INSTALL_DIR/service/log/run"

# ── 2. Install Python dependencies ───────────────────────────────────────────────
echo "[2/4] Installing aioesphomeapi and dependencies to $INSTALL_DIR/vendor/ …"
mkdir -p "$INSTALL_DIR/vendor"

# Require pip3; Venus OS v3.x ships with it
if ! command -v pip3 &>/dev/null; then
    echo "ERROR: pip3 not found. Ensure Venus OS v3.x is installed."
    exit 1
fi

pip3 install \
    --quiet \
    --target "$INSTALL_DIR/vendor" \
    --upgrade \
    "aioesphomeapi>=18.0.0"

echo "    Dependencies installed."

# ── 3. Register daemontools service ─────────────────────────────────────────────
echo "[3/4] Registering daemontools service …"

# Stop any running instance first
if [ -L "$SERVICE_DIR/dbus-esphome" ]; then
    svc -d "$SERVICE_DIR/dbus-esphome" 2>/dev/null || true
    rm -f "$SERVICE_DIR/dbus-esphome"
fi

ln -sf "$INSTALL_DIR/service" "$SERVICE_DIR/dbus-esphome"
echo "    Service linked: $SERVICE_DIR/dbus-esphome → $INSTALL_DIR/service"

# ── 4. Hook rc.local for persistence across firmware updates ─────────────────────
echo "[4/4] Writing /data/rc.local hook for update persistence …"

RC_LOCAL="/data/rc.local"
HOOK_MARKER="# dbus-esphome service link"
HOOK_CMD="ln -sf $INSTALL_DIR/service $SERVICE_DIR/dbus-esphome"

if ! grep -qF "dbus-esphome" "$RC_LOCAL" 2>/dev/null; then
    {
        echo ""
        echo "$HOOK_MARKER"
        echo "$HOOK_CMD"
    } >> "$RC_LOCAL"
    chmod +x "$RC_LOCAL"
    echo "    Hook added to $RC_LOCAL"
else
    echo "    Hook already present in $RC_LOCAL"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit $INSTALL_DIR/config.ini"
echo "     – Set host = <IP address of your ESPHome device>"
echo "     – Set encryption_key = <api.encryption.key from your ESPHome YAML>"
echo "     – Add more [device_N] sections for additional devices"
echo ""
echo "  2. Start the service:"
echo "     svc -u $SERVICE_DIR/dbus-esphome"
echo ""
echo "  3. Check logs:"
echo "     tail -f /var/log/dbus-esphome/current"
echo ""
echo "  4. Verify dbus services are registered:"
echo "     dbus -y | grep esphome"
echo ""
