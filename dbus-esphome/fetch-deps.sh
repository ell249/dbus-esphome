#!/bin/bash
# Run this on your computer (Mac or Linux) BEFORE copying files to the GX device.
# It downloads aioesphomeapi and all dependencies into ./vendor/ so they can be
# transferred along with the driver without needing pip3 on Venus OS.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$SCRIPT_DIR/vendor"

if ! command -v pip3 &>/dev/null; then
    echo "ERROR: pip3 not found. Install Python 3 on your computer and try again."
    exit 1
fi

echo "Downloading aioesphomeapi and dependencies into $VENDOR_DIR …"
pip3 install \
    --quiet \
    --target "$VENDOR_DIR" \
    --upgrade \
    --no-binary protobuf \
    "aioesphomeapi>=18.0.0"

echo "Done. Now copy the dbus-esphome/ folder (including vendor/) to the GX device:"
echo "  scp -r dbus-esphome/ root@<gx-ip>:/tmp/"
