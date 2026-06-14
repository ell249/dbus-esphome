#!/bin/bash
# uninstall.sh  –  Remove dbus-esphome from Venus OS
#
# Run on the Cerbo GX via SSH:
#   bash /data/dbus-esphome/uninstall.sh

set -e

INSTALL_DIR="/data/dbus-esphome"
SERVICE_DIR="/service"
RC_LOCAL="/data/rc.local"

echo "=== dbus-esphome uninstaller ==="

# Stop and remove service symlink
if [ -L "$SERVICE_DIR/dbus-esphome" ]; then
    echo "Stopping service …"
    svc -d "$SERVICE_DIR/dbus-esphome" 2>/dev/null || true
    rm -f "$SERVICE_DIR/dbus-esphome"
    echo "Service removed."
else
    echo "Service symlink not found (already removed?)."
fi

# Remove rc.local hook
if grep -qF "dbus-esphome" "$RC_LOCAL" 2>/dev/null; then
    echo "Removing /data/rc.local hook …"
    # Remove the marker comment and the ln command that follows it
    grep -v "dbus-esphome" "$RC_LOCAL" > "${RC_LOCAL}.tmp" && mv "${RC_LOCAL}.tmp" "$RC_LOCAL"
    echo "Hook removed."
fi

# Remove installation directory (ask for confirmation)
echo ""
read -r -p "Remove $INSTALL_DIR (including config.ini)? [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    rm -rf "$INSTALL_DIR"
    echo "Removed $INSTALL_DIR"
else
    echo "Keeping $INSTALL_DIR (driver files remain but service is stopped)"
fi

echo ""
echo "=== Uninstall complete ==="
