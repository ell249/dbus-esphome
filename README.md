# dbus-esphome

A generic [ESPHome](https://esphome.io) → [Victron Venus OS](https://github.com/victronenergy/venus) bridge that exposes any ESPHome device's entities as native dbus services on a Victron GX device (Cerbo GX, Venus GX, etc.).

Entities appear in the Venus OS Remote Console and are controllable from the GUI — no modification to the Venus OS firmware is required beyond installing this driver.

> **Status: early testing** — This project is brand new and currently being tested for the first time. Expect rough edges; feedback and bug reports are very welcome.

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow?logo=buymeacoffee)](https://buymeacoffee.com/ell249)

---

## How it works

The driver connects to one or more ESPHome devices using the **native encrypted API** (port 6053, the same protocol used by Home Assistant). On connect it automatically discovers every entity the device exposes and registers them as Venus OS dbus services. State changes flow in both directions:

- ESPHome → Venus OS: entity state updates are pushed immediately via the native API subscription
- Venus OS → ESPHome: switch toggles and brightness changes written in the GUI are sent back as API commands

No polling. No HTTP. The connection is persistent and encrypted.

---

## Supported entity types

| ESPHome entity | Venus OS dbus service | GUI appearance |
|---|---|---|
| **Switch** | `com.victronenergy.relay.esphome_{name}` | Relay toggle |
| **Light** (on/off) | same relay service | Relay toggle |
| **Light** (dimmable) | same relay service | Relay toggle + `/Relay/N/Brightness` (0–100) |
| **Sensor** — temperature | `com.victronenergy.temperature.esphome_{name}_{n}` | Temperature tile |
| **Sensor** — current / voltage / analog | `com.victronenergy.tank.esphome_{name}_{n}` | Tank level (%) |
| **Binary sensor** | relay service `/Digital/N/State` | Read-only indicator |

Switches and lights are grouped into a single relay service per device. Temperature sensors each get their own temperature service. All other numeric sensors are represented as tank-level services, with the raw value scaled to a 0–100% level using configurable or unit-based defaults.

The entity's `name:` field from the ESPHome YAML becomes the label in the Venus OS GUI.

---

## dbus service naming

Services are named using the ESPHome device name (the `name:` field in your ESPHome YAML) with hyphens replaced by underscores:

```
com.victronenergy.relay.esphome_my_device
com.victronenergy.temperature.esphome_my_device_0
com.victronenergy.tank.esphome_my_device_0
```

This makes services immediately identifiable in `dbus-spy` and log output.

---

## Multi-device support

Any number of ESPHome devices can be bridged by a single driver instance. Each device is listed as a `[device_N]` section in `config.ini` and runs as an independent connection task. All devices share one process; services from different devices do not conflict because the device name is part of the service name.

---

## Reconnection and watchdog

The driver reconnects automatically after any connection failure, with a 30-second retry interval. While a device is disconnected its dbus services remain registered but `/Connected` is set to 0, so Venus OS shows the device as offline rather than removing it from the GUI.

On the ESPHome side, adding `reboot_timeout: 15min` to the `api:` block causes the device to reboot if no client connects within 15 minutes — a useful watchdog to recover from silent firmware hangs:

```yaml
api:
  encryption:
    key: "your-key-here"
  reboot_timeout: 15min
```

---

## Tank level scaling

Current, voltage, and other numeric sensors are exposed as tank levels (0–100%). The raw sensor value is available at `/RawValue` alongside the unit at `/RawUnit`. The scaling range defaults are:

| Unit | Default max |
|---|---|
| A | 50 |
| mA | 50 000 |
| V | 30 |
| mV | 30 000 |
| W | 5 000 |
| % | 100 |
| others | 100 |

Per-sensor overrides are set in the `[sensor_ranges]` section of `config.ini`.

---

## Requirements

**Venus OS device (Cerbo GX, Venus GX, etc.):**
- Venus OS v3.x (Python 3.11, pip3 available)
- Internet access during installation (to download `aioesphomeapi`)

**ESPHome device:**
- ESPHome firmware with the `api:` component enabled
- For encrypted connections: `api.encryption.key` set in YAML
- Port 6053 reachable from the Venus OS device

---

## Files

```
dbus-esphome/
  dbus-esphome.py       Main driver
  config.ini            Device configuration
  install.sh            Installer (run on Venus OS via SSH)
  uninstall.sh          Uninstaller
  service/
    run                 daemontools service runner
    log/run             daemontools log runner
```

---

## Licence

Design, architecture and requirements by Elliot Alfirevich. Code written with assistance from Claude Code.

MIT — see [LICENSE.md](LICENSE.md)
