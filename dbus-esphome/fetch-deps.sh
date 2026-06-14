#!/bin/bash
# Run on your computer (Mac or Linux) BEFORE copying files to the GX device.
# Uses Docker to install aioesphomeapi inside a native linux/arm/v7 + Python 3.12
# container, producing a vendor/ directory that runs correctly on Venus OS.
#
# Requires Docker Desktop: https://docs.docker.com/desktop/install/mac-install/

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$SCRIPT_DIR/vendor"

if ! command -v docker &>/dev/null || ! docker info &>/dev/null 2>&1; then
    echo "ERROR: Docker is required but is not running."
    echo "Install Docker Desktop from https://docs.docker.com/desktop/install/mac-install/"
    echo "then start it and re-run this script."
    exit 1
fi

rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"

echo "Fetching aioesphomeapi for linux/arm/v7 / Python 3.12 via Docker …"
echo "(First run will pull the python:3.12-slim image — this may take a minute)"

docker run --rm \
    --platform linux/arm/v7 \
    -v "$VENDOR_DIR:/vendor" \
    python:3.12-slim \
    sh -c "set -e
        apt-get update -qq
        apt-get install -y -qq --no-install-recommends gcc libc6-dev libffi-dev
        pip install --quiet --target /vendor 'aioesphomeapi>=18.0.0' tzdata
        cp -r /usr/local/lib/python3.12/zoneinfo /vendor/
    "

echo ""
echo "Done. Copy the dbus-esphome/ folder (including vendor/) to the GX device:"
echo "  scp -r dbus-esphome/ root@<gx-ip>:/tmp/"
