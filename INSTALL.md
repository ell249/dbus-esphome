# Installation

## Prerequisites

- A Victron GX device (Cerbo GX, Venus GX, Ekrano GX, etc.) running **Venus OS v3.x**
- SSH access to the GX device
- Internet access on the GX device at install time (to download Python dependency wheels)
  — or, for air-gapped installs, see the [Offline / air-gapped install](#offline--air-gapped-install) section below
- One or more ESPHome devices with the `api:` component enabled, reachable on the local network

---

## 1. Prepare your ESPHome device

Ensure the `api:` component is present in your ESPHome YAML. Adding `reboot_timeout` is recommended as a watchdog:

```yaml
api:
  encryption:
    key: "your-32-byte-base64-key"
  reboot_timeout: 15min
```

If you do not want API encryption, omit the `encryption:` block and leave `encryption_key` empty in `config.ini`. Note that the ESPHome native API without encryption is still only accessible on the local network.

Flash the updated firmware if you added `reboot_timeout`.

---

## 2. Enable SSH on the GX device

SSH is disabled by default on Venus OS and must be turned on before you can connect.

**Via the Remote Console (web browser or local screen):**

1. Open the Remote Console — navigate to **Settings → General**
2. Set **Access Level** to **Superuser** (required to enable SSH)
3. Go to **Settings → Remote Console** and ensure Remote Console is enabled if you are using the web interface
4. Go to **Settings → General → SSH** and set it to **On**

**Or via the Venus OS local touch display (if fitted):**

1. Tap the menu icon → **Settings → General**
2. Set Access Level to **Superuser**, then enable **SSH**

Once SSH is enabled, the root account has no password by default — you can connect immediately:

```bash
ssh root@192.168.x.x
```

> If you cannot connect, confirm your GX device and computer are on the same local network and that you have the correct IP address (visible under **Settings → Ethernet** or **Settings → Wi-Fi**).

---

## 3. Copy the driver to the GX device

From your computer, copy the `dbus-esphome/` folder to the GX device:

```bash
scp -r dbus-esphome/ root@192.168.x.x:/tmp/
```

You do not need to run anything on your computer first. Python dependencies are fetched automatically during the next step.

---

## 4. Run the installer

SSH into the GX device and run:

```bash
ssh root@192.168.x.x
bash /tmp/dbus-esphome/install.sh
```

The installer:
1. Copies the driver files to `/data/dbus-esphome/` (this partition survives firmware updates)
2. Downloads `aioesphomeapi` and its dependencies as pre-built wheels into `/data/dbus-esphome/vendor/`, automatically selecting the correct build for the device's architecture and Python version (no pip required)
3. Creates a daemontools service symlink at `/service/dbus-esphome`
4. Adds a hook to `/data/rc.local` so the service symlink is re-created automatically after any firmware update

Dependencies are installed once and stored under `/data/`, so they also survive firmware updates.

---

## 5. Edit the configuration

```bash
nano /data/dbus-esphome/config.ini
```

At minimum, set the IP address and encryption key for each device:

```ini
[device_1]
host = 192.168.1.100
encryption_key = ZW0RlpPGAy5OXkWyulqFNnGBkaA5jKEuOQFvafwlo3w=

# Add more devices as needed:
# [device_2]
# host = 192.168.1.101
# encryption_key = <key from ESPHome YAML>
```

The `encryption_key` value is the base64 string from `api.encryption.key` in your ESPHome YAML. Leave it empty if the device has no API encryption.

### Optional: sensor range overrides

Numeric sensors (current, voltage, analog inputs) are displayed as tank levels (0–100%). The default scaling is unit-based (e.g. A → 0–50 A). To override for a specific sensor, add entries to `[sensor_ranges]` using the sensor's `name:` from the ESPHome YAML:

```ini
[sensor_ranges]
Current Draw.max = 30
Battery Voltage.max = 16
```

---

## 6. Start the service

```bash
svc -u /service/dbus-esphome
```

---

## 7. Verify

**Check logs:**
```bash
tail -f /var/log/dbus-esphome/current
```

You should see output like:
```
12:34:56 INFO    dbus-esphome  Starting dbus-esphome with 1 device(s)
12:34:56 INFO    dbus-esphome  [192.168.1.100] Connecting …
12:34:57 INFO    dbus-esphome  [192.168.1.100] Connected → My Device (my_device)
12:34:57 INFO    dbus-esphome  [192.168.1.100] Switch service: 10 outputs, 1 digital inputs
12:34:57 INFO    dbus-esphome  [192.168.1.100] Temperature service: Temperature 1
```

**Check dbus services are registered:**
```bash
dbus -y | grep esphome
```

**Check a switch output value:**
```bash
dbus -y com.victronenergy.switch.esphome_my_device /SwitchableOutput/0/State GetValue
```

**Toggle a switch from the command line:**
```bash
dbus -y com.victronenergy.switch.esphome_my_device /SwitchableOutput/0/State SetValue 1
dbus -y com.victronenergy.switch.esphome_my_device /SwitchableOutput/0/State SetValue 0
```

Open the Venus OS Remote Console — switches and sensors from your ESPHome device should appear in the relevant panels.

---

## Updating the driver

To update to a newer version of the driver:

1. Copy the new files to the GX device
2. Run `install.sh` again — it preserves your existing `config.ini` and skips reinstalling dependencies if they are already present
3. Restart the service: `svc -t /service/dbus-esphome`

To force a reinstall of Python dependencies (e.g. to pick up a newer `aioesphomeapi`):

```bash
rm -rf /data/dbus-esphome/vendor
bash /tmp/dbus-esphome/install.sh
svc -t /service/dbus-esphome
```

---

## Offline / air-gapped install

If the GX device has no internet access, you can build a dependency bundle on your computer and transfer it alongside the driver.

> **Requires Docker Desktop** — [docs.docker.com/desktop](https://docs.docker.com/desktop/install/mac-install/)

`fetch-deps.sh` SSHes to the GX device to detect its architecture and Python version, then uses Docker to download the correct dependency wheels:

```bash
bash dbus-esphome/fetch-deps.sh <gx-ip>
```

Example:
```bash
bash dbus-esphome/fetch-deps.sh 192.168.1.50
# Detecting architecture and Python version from 192.168.1.50 …
#   Detected: arch=armv7l  python=Python 3.12.9
# Building vendor/ for platform=linux/arm/v7  python=3.12
```

If you prefer to specify the values manually instead of auto-detecting:

```bash
bash dbus-esphome/fetch-deps.sh --manual armv7 312    # ARMv7 + Python 3.12
bash dbus-esphome/fetch-deps.sh --manual aarch64 312  # ARM64 + Python 3.12
```

To check arch and Python version on the device yourself:
```bash
ssh root@<gx-ip> 'uname -m && python3 --version'
```

Once `fetch-deps.sh` completes, copy the full folder (now including `vendor/`) to the GX device and run `install.sh` as normal — it detects the pre-built `vendor/` and uses it directly.

```bash
scp -r dbus-esphome/ root@<gx-ip>:/tmp/
ssh root@<gx-ip> bash /tmp/dbus-esphome/install.sh
```

---

## Uninstalling

```bash
bash /data/dbus-esphome/uninstall.sh
```

This stops the service, removes the daemontools symlink, and removes the `/data/rc.local` hook. You will be asked whether to delete the `/data/dbus-esphome/` directory.

---

## Troubleshooting

**Service won't start / crashes immediately:**
Check `tail -f /var/log/dbus-esphome/current` for the error. Common causes:
- `aioesphomeapi` not installed — re-run `install.sh`. If the device has no internet access, use `fetch-deps.sh` first (see [Offline install](#offline--air-gapped-install) above)
- `velib_python` not found — check that Venus OS is v3.x and the path `/opt/victronenergy/dbus-systemcalc-py/ext/velib_python` exists

**Device shows as offline (`/Connected = 0`):**
- Confirm the ESPHome device is powered and on the network
- Confirm `host` in `config.ini` is correct
- Confirm port 6053 is not firewalled
- Check that `encryption_key` matches `api.encryption.key` in the ESPHome YAML exactly

**Entity names are wrong or missing:**
The entity names come directly from the ESPHome `name:` field. Entities marked `internal: true` in the ESPHome YAML are not exposed via the API and will not appear.

**Service disappears after firmware update:**
The `/data/rc.local` hook should handle this automatically. If it doesn't, re-run `install.sh`.
