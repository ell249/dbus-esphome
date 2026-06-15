#!/bin/bash
# fetch-deps.sh  –  Build an offline vendor/ bundle for air-gapped installs.
#
# ONLY needed if the target device has no internet access.
# For normal installs, install.sh fetches dependencies via pip on the device
# automatically — you do not need to run this script first.
#
# Usage:
#   ./fetch-deps.sh <gx-ip>               # SSH to device to auto-detect arch + Python
#   ./fetch-deps.sh --manual armv7 312    # specify manually (armv7|aarch64, e.g. 312)
#
# To check arch + Python on your device manually:
#   ssh root@<gx-ip> 'uname -m && python3 --version'
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

# ── Parse arguments ───────────────────────────────────────────────────────────

RAW_ARCH=""
RAW_PY=""

if [ "${1:-}" = "--manual" ]; then
    # Manual mode: ./fetch-deps.sh --manual <arch> <pyver>
    ARCH="${2:-}"
    PYVER="${3:-}"
    if [ -z "$ARCH" ] || [ -z "$PYVER" ]; then
        echo "Usage: $0 --manual <armv7|aarch64> <python_version>"
        echo "  Example: $0 --manual armv7 312"
        exit 1
    fi
    case "$ARCH" in
        armv7)  RAW_ARCH="armv7l" ;;
        aarch64) RAW_ARCH="aarch64" ;;
        *)
            echo "ERROR: Unknown arch '$ARCH'. Use armv7 or aarch64."
            exit 1
            ;;
    esac
    RAW_PY="Python ${PYVER:0:1}.${PYVER:1}"
elif [ -n "${1:-}" ]; then
    # Auto-detect mode: SSH to the device
    GX_IP="$1"
    echo "Detecting architecture and Python version from $GX_IP …"
    DETECT=$(ssh -o ConnectTimeout=5 root@"$GX_IP" 'printf "%s\n%s\n" "$(uname -m)" "$(python3 --version 2>&1)"') || {
        echo "ERROR: Could not SSH to root@$GX_IP"
        echo "Check the IP address and that SSH is enabled on the device."
        exit 1
    }
    RAW_ARCH=$(echo "$DETECT" | sed -n '1p')
    RAW_PY=$(echo "$DETECT"   | sed -n '2p')
    echo "  Detected: arch=$RAW_ARCH  python=$RAW_PY"
else
    echo "Usage:"
    echo "  $0 <gx-ip>                    # auto-detect arch + Python from device"
    echo "  $0 --manual armv7 312         # specify manually"
    echo ""
    echo "To check on your device:  ssh root@<gx-ip> 'uname -m && python3 --version'"
    exit 1
fi

# ── Map to Docker parameters ──────────────────────────────────────────────────

case "$RAW_ARCH" in
    armv7l)  PLATFORM="linux/arm/v7" ;;
    aarch64) PLATFORM="linux/arm64" ;;
    *)
        echo "ERROR: Unsupported architecture '$RAW_ARCH'."
        echo "Supported: armv7l (armv7), aarch64"
        exit 1
        ;;
esac

PY_TAG=$(echo "$RAW_PY" | grep -oE '[0-9]+\.[0-9]+' | head -1)
if [ -z "$PY_TAG" ]; then
    echo "ERROR: Could not parse Python version from: $RAW_PY"
    exit 1
fi

echo ""
echo "Building vendor/ for platform=$PLATFORM  python=$PY_TAG"
echo "(First run will pull the python:${PY_TAG}-slim image — this may take a minute)"
echo ""

# ── Build vendor/ via Docker ──────────────────────────────────────────────────

rm -rf "$VENDOR_DIR"
mkdir -p "$VENDOR_DIR"

docker run --rm \
    --platform "$PLATFORM" \
    -v "$VENDOR_DIR:/vendor" \
    "python:${PY_TAG}-slim" \
    sh -c "set -e
        apt-get update -qq
        apt-get install -y -qq --no-install-recommends gcc libc6-dev libffi-dev
        pip install --quiet --target /vendor 'aioesphomeapi>=18.0.0'
    "

echo ""
echo "Done. Copy the dbus-esphome/ folder (including vendor/) to the GX device:"
echo "  scp -r dbus-esphome/ root@<gx-ip>:/tmp/"
echo ""
echo "Then run install.sh on the device — it will use the bundled vendor/ automatically."
