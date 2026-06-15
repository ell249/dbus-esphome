#!/usr/bin/env python3
"""
dbus-esphome  –  Generic ESPHome → Victron Venus OS dbus bridge.

Connects to one or more ESPHome devices via the native encrypted API (port 6053),
auto-discovers every entity, and registers them as Venus OS dbus services:

  Switches / Lights (on/off)   → com.victronenergy.switch.esphome_{name}
                                    /SwitchableOutput/N/State           (writeable, 0/1)
                                    /SwitchableOutput/N/Name
                                    /SwitchableOutput/N/Settings/ShowUIControl = 1
                                    /SwitchableOutput/N/Settings/Type   = 1 (TOGGLE)
                                    /SwitchableOutput/N/Settings/Function = 2 (MANUAL)
  Lights (dimmable)            → same switch service
                                    /SwitchableOutput/N/Brightness      (writeable, 0-100)
  Temperature sensors          → com.victronenergy.temperature.esphome_{name}_{n}
                                    /Temperature
  Current / voltage / analog   → com.victronenergy.tank.esphome_{name}_{n}
                                    /Level  /RawValue  /RawUnit  /RawLower  /RawUpper
  Binary sensors               → switch service
                                    /Digital/N/State         (read-only, 0/1)
                                    /Digital/N/Name

The device's ESPHome name (e.g. "esp32-can-io") is used in the dbus service name
with hyphens replaced by underscores: esphome_esp32_can_io.

Run as a daemontools service on Venus OS, installed to /data/dbus-esphome/.
"""

import asyncio
import configparser
import logging
import os
import sys
import threading
import traceback

import dbus

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

# ── velib_python  (Venus OS utility library for dbus service registration) ──────
_VELIB_SEARCH = [
    '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python',
    '/opt/victronenergy/vehicle-control/ext/velib_python',
    '/opt/victronenergy/dbus-mqtt/ext/velib_python',
    os.path.join(os.path.dirname(__file__), 'velib_python'),  # local copy fallback
]
for _p in _VELIB_SEARCH:
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
        break

from vedbus import VeDbusService  # noqa: E402  (after path manipulation)

# ── aioesphomeapi  (vendored into ./vendor/ by install.sh) ──────────────────────
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from aioesphomeapi import APIClient                       # noqa: E402
from aioesphomeapi.model import (                         # noqa: E402
    SwitchInfo, SwitchState,
    LightInfo, LightState,
    SensorInfo, SensorState,
    BinarySensorInfo, BinarySensorState,
    ButtonInfo,
    NumberInfo, NumberState,
    TextSensorInfo,
)

# ── Logging ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s %(name)s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('dbus-esphome')

# ── Sensor scaling defaults by unit ─────────────────────────────────────────────
_UNIT_MAX = {
    'A':   50.0,
    'mA':  50000.0,
    'V':   30.0,
    'mV':  30000.0,
    'W':   5000.0,
    'kW':  5.0,
    '%':   100.0,
    'lx':  10000.0,
    'ppm': 5000.0,
}
_TEMPERATURE_CLASSES = frozenset({'temperature'})


def _light_is_dimmable(entity: LightInfo) -> bool:
    """Return True if this light supports brightness/dimming control.

    Older aioesphomeapi used a 'supports_brightness' bool; newer versions
    replaced it with 'supported_color_modes', a list of LightColorCapability
    bitmask values where bit 1 (value 2) indicates brightness support.
    """
    if getattr(entity, 'supports_brightness', False):
        return True
    for mode in getattr(entity, 'supported_color_modes', ()):
        try:
            if int(mode) & 2:  # LightColorCapability.BRIGHTNESS = 2
                return True
        except (TypeError, ValueError):
            pass
    return False


# ─────────────────────────────────────────────────────────────────────────────────
class DeviceConnection:
    """
    Manages the lifecycle of a single ESPHome device connection and its
    corresponding Venus OS dbus services.

    Created once; services persist across reconnects so Venus OS GUI entries
    do not disappear and reappear on every reconnect.
    """

    def __init__(
        self,
        host: str,
        encryption_key: str,
        sensor_ranges: dict,
        async_loop: asyncio.AbstractEventLoop,
        device_idx: int,
    ):
        self.host = host
        self.encryption_key = encryption_key
        self.sensor_ranges = sensor_ranges
        self._loop = async_loop
        self._idx = device_idx

        # Set by _build_services() on first successful connect
        self._relay_svc: VeDbusService | None = None
        self._temp_svcs: dict[int, VeDbusService] = {}   # entity_key → service
        self._tank_svcs: dict[int, tuple] = {}            # entity_key → (service, max_val)
        self._entity_map: dict[int, tuple] = {}           # key → (svc, path[, extra])
        self._services_built = False

        # Current live client (replaced on every reconnect)
        self._client: APIClient | None = None

    # ── Public entry point ───────────────────────────────────────────────────────

    async def run(self):
        """Run forever, reconnecting after any failure."""
        while True:
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('[%s] %s: %s\n%s', self.host, type(exc).__name__, exc,
                            traceback.format_exc())

            log.info('[%s] Reconnecting in 30 s …', self.host)
            GLib.idle_add(self._set_connected, False)
            await asyncio.sleep(30)

    # ── Connection lifecycle ─────────────────────────────────────────────────────

    async def _connect_and_run(self):
        log.info('[%s] Connecting …', self.host)
        self._client = APIClient(
            self.host, 6053, None, noise_psk=self.encryption_key
        )

        await asyncio.wait_for(self._client.connect(login=True), timeout=30)
        log.debug('[%s] Handshake complete', self.host)

        device_info = await asyncio.wait_for(self._client.device_info(), timeout=10)
        dev_name = device_info.name.replace('-', '_').replace(' ', '_')
        friendly = device_info.friendly_name or device_info.name
        log.info('[%s] Connected → %s (%s)', self.host, friendly, dev_name)

        entities, _ = await asyncio.wait_for(
            self._client.list_entities_services(), timeout=10
        )

        if not self._services_built:
            # VeDbusService path registration must run in the GLib main thread.
            # Schedule it there via idle_add, then await completion without
            # blocking the asyncio loop (which must stay live for ESPHome keepalives).
            done = threading.Event()
            exc_box = []

            def _build_in_glib():
                try:
                    self._build_services(entities, dev_name, friendly)
                except Exception as e:
                    exc_box.append(e)
                finally:
                    done.set()
                return False  # GLib.SOURCE_REMOVE

            GLib.idle_add(_build_in_glib)
            await asyncio.get_event_loop().run_in_executor(None, done.wait)
            if exc_box:
                raise exc_box[0]
            self._services_built = True

        GLib.idle_add(self._set_connected, True)

        sub = self._client.subscribe_states(self._on_state)
        if asyncio.iscoroutine(sub):
            await asyncio.wait_for(sub, timeout=10)

        # Heartbeat: detect silent TCP drops by polling device info every 30 s
        while True:
            await asyncio.sleep(30)
            await asyncio.wait_for(self._client.device_info(), timeout=10)

    # ── dbus service construction ────────────────────────────────────────────────

    def _build_services(self, entities, dev_name: str, friendly: str):
        """
        Called once after first successful entity discovery.
        Creates Venus OS dbus services for every discovered entity type.
        """
        base = self._idx * 1000

        switches      = [e for e in entities if isinstance(e, SwitchInfo)]
        lights        = [e for e in entities if isinstance(e, LightInfo)]
        buttons       = [e for e in entities if isinstance(e, ButtonInfo)]
        numbers       = [e for e in entities if isinstance(e, NumberInfo)]
        temp_sensors  = [e for e in entities if isinstance(e, SensorInfo)
                         and e.device_class in _TEMPERATURE_CLASSES]
        other_sensors = [e for e in entities if isinstance(e, SensorInfo)
                         and e.device_class not in _TEMPERATURE_CLASSES]
        binary_snsr   = [e for e in entities if isinstance(e, BinarySensorInfo)]

        _handled = {SwitchInfo, LightInfo, ButtonInfo, NumberInfo,
                    SensorInfo, BinarySensorInfo, TextSensorInfo}
        skipped = [e for e in entities if type(e) not in _handled]
        log.info('[%s] All entities (%d): %s', self.host, len(entities),
                 ', '.join(f'{type(e).__name__}:{e.name}' for e in entities))
        if skipped:
            log.info('[%s] Skipped (unsupported type, %d): %s', self.host, len(skipped),
                     ', '.join(f'{type(e).__name__}:{e.name}' for e in skipped))

        # ── Switch service: switches, lights, binary sensors ─────────────────────
        # Each VeDbusService needs its own private bus connection so they each get
        # a clean '/' object-path namespace; sharing dbus.SystemBus() means they
        # all fight over the one '/' slot on the shared connection.
        rs = VeDbusService(
            f'com.victronenergy.switch.esphome_{dev_name}',
            bus=dbus.SystemBus(private=True),
            register=False,
        )
        rs.register()
        rs.add_path('/DeviceInstance', base)
        rs.add_path('/ProductName', friendly)
        rs.add_path('/FirmwareVersion', 0)
        rs.add_path('/Connected', 0)
        rs.add_path('/Serial', dev_name)

        output_idx = 0
        for entity in (*switches, *lights, *buttons):
            is_button = isinstance(entity, ButtonInfo)
            dimmable = isinstance(entity, LightInfo) and _light_is_dimmable(entity)
            out_type = 2 if dimmable else 1  # 1=TOGGLE, 2=DIMMABLE

            path = f'/SwitchableOutput/{output_idx}/State'
            rs.add_path(f'/SwitchableOutput/{output_idx}/Name', entity.name)
            rs.add_path(f'/SwitchableOutput/{output_idx}/Status', 0)
            rs.add_path(f'/SwitchableOutput/{output_idx}/Settings/ShowUIControl', 1, writeable=True)
            rs.add_path(f'/SwitchableOutput/{output_idx}/Settings/Type', out_type, writeable=True)
            rs.add_path(f'/SwitchableOutput/{output_idx}/Settings/Function', 2, writeable=True)
            rs.add_path(
                path, 0,
                writeable=True,
                onchangecallback=self._make_toggle_cb(entity, rs),
            )
            mapping = (rs, path)

            if dimmable:
                bpath = f'/SwitchableOutput/{output_idx}/Brightness'
                rs.add_path(
                    bpath, 0,
                    writeable=True,
                    onchangecallback=self._make_brightness_cb(entity),
                )
                mapping = (rs, path, bpath)

            if not is_button:
                # Buttons have no state updates; only switches/lights need entity_map
                self._entity_map[entity.key] = mapping
            output_idx += 1

        for num_idx, entity in enumerate(numbers):
            path = f'/Number/{num_idx}/Value'
            rs.add_path(f'/Number/{num_idx}/Name', entity.name)
            rs.add_path(f'/Number/{num_idx}/Min', entity.min_value)
            rs.add_path(f'/Number/{num_idx}/Max', entity.max_value)
            rs.add_path(f'/Number/{num_idx}/Step', entity.step)
            rs.add_path(f'/Number/{num_idx}/Unit', entity.unit_of_measurement or '')
            rs.add_path(
                path, None,
                writeable=True,
                onchangecallback=self._make_number_cb(entity),
            )
            self._entity_map[entity.key] = (rs, path)

        for idx, entity in enumerate(binary_snsr):
            path = f'/Digital/{idx}/State'
            rs.add_path(f'/Digital/{idx}/Name', entity.name)
            rs.add_path(path, 0)
            self._entity_map[entity.key] = (rs, path)

        self._relay_svc = rs

        log.info(
            '[%s] Switch service: %d outputs (%d buttons, %d numbers), %d digital inputs',
            self.host, output_idx, len(buttons), len(numbers), len(binary_snsr),
        )

        # ── Temperature services ─────────────────────────────────────────────────
        for idx, entity in enumerate(temp_sensors):
            svc = VeDbusService(
                f'com.victronenergy.temperature.esphome_{dev_name}_{idx}',
                bus=dbus.SystemBus(private=True),
                register=False,
            )
            svc.register()
            svc.add_path('/DeviceInstance', base + 100 + idx)
            svc.add_path('/ProductName', entity.name)
            svc.add_path('/Connected', 0)
            svc.add_path('/Temperature', None)
            svc.add_path('/Status', 0)
            self._temp_svcs[entity.key] = svc
            self._entity_map[entity.key] = (svc, '/Temperature')
            log.info('[%s] Temperature service: %s', self.host, entity.name)

        # ── Tank (level) services ─────────────────────────────────────────────────
        for idx, entity in enumerate(other_sensors):
            raw_max = self._sensor_max(entity)
            svc = VeDbusService(
                f'com.victronenergy.tank.esphome_{dev_name}_{idx}',
                bus=dbus.SystemBus(private=True),
                register=False,
            )
            svc.register()
            svc.add_path('/DeviceInstance', base + 200 + idx)
            svc.add_path('/ProductName', entity.name)
            svc.add_path('/Connected', 0)
            svc.add_path('/Level', None)
            svc.add_path('/RawValue', None)
            svc.add_path('/RawUnit', entity.unit_of_measurement or '')
            svc.add_path('/RawLower', 0.0)
            svc.add_path('/RawUpper', raw_max)
            svc.add_path('/Status', 0)
            self._tank_svcs[entity.key] = (svc, raw_max)
            self._entity_map[entity.key] = (svc, '/RawValue', raw_max)
            log.info(
                '[%s] Tank service: %s (max=%.1f %s)',
                self.host, entity.name, raw_max, entity.unit_of_measurement or '',
            )

    def _sensor_max(self, entity: SensorInfo) -> float:
        """Return configured or unit-default maximum for tank level scaling."""
        for key in (entity.name, getattr(entity, 'object_id', None)):
            if key and key in self.sensor_ranges:
                return float(self.sensor_ranges[key])
        return _UNIT_MAX.get(entity.unit_of_measurement or '', 100.0)

    # ── State update handler (called from asyncio thread) ────────────────────────

    def _on_state(self, state):
        key = state.key
        if key not in self._entity_map:
            return
        mapping = self._entity_map[key]

        if isinstance(state, (SwitchState, BinarySensorState)):
            svc, path = mapping[0], mapping[1]
            val = 1 if state.state else 0
            GLib.idle_add(self._dbus_set, svc, path, val)

        elif isinstance(state, LightState):
            svc, path = mapping[0], mapping[1]
            if len(mapping) > 2:
                # Dimmable (Type=2): State IS the brightness (0=off, 1-100=on at level).
                # Writing boolean 1 to a dimmer path means "1%" which renders as off.
                brightness_pct = max(1, round(state.brightness * 100)) if state.state else 0
                GLib.idle_add(self._dbus_set, svc, path, brightness_pct)
                GLib.idle_add(self._dbus_set, svc, mapping[2], brightness_pct)
            else:
                GLib.idle_add(self._dbus_set, svc, path, 1 if state.state else 0)

        elif isinstance(state, NumberState):
            svc, path = mapping[0], mapping[1]
            GLib.idle_add(self._dbus_set, svc, path, state.state)

        elif isinstance(state, SensorState) and state.state is not None:
            svc, path = mapping[0], mapping[1]
            val = state.state
            if path == '/Temperature':
                GLib.idle_add(self._dbus_set, svc, '/Temperature', val)
            else:
                raw_max = mapping[2]
                level = min(100.0, max(0.0, (val / raw_max) * 100)) if raw_max else 0.0

                def _update_tank(s=svc, v=val, l=round(level, 1)):
                    s['/RawValue'] = v
                    s['/Level'] = l
                    return False

                GLib.idle_add(_update_tank)

    # ── dbus write callbacks (called from GLib thread) ───────────────────────────

    def _make_toggle_cb(self, entity, svc):
        """Return an onchangecallback that sends on/off/press to ESPHome."""
        def cb(path, value):
            if self._client is None:
                return False
            state = bool(value)

            async def _send():
                try:
                    if isinstance(entity, SwitchInfo):
                        result = self._client.switch_command(
                            key=entity.key, state=state
                        )
                        if asyncio.iscoroutine(result):
                            await result
                    elif isinstance(entity, ButtonInfo):
                        # Buttons are momentary: press on rising edge, reset state to 0
                        if state:
                            result = self._client.button_command(key=entity.key)
                            if asyncio.iscoroutine(result):
                                await result
                        GLib.idle_add(self._dbus_set, svc, path, 0)
                    elif _light_is_dimmable(entity):
                        # Dimmable: Venus OS writes brightness 0-100 to State path
                        brightness = max(0.0, min(1.0, float(value) / 100.0))
                        result = self._client.light_command(
                            key=entity.key,
                            state=value > 0,
                            brightness=brightness if value > 0 else None,
                        )
                        if asyncio.iscoroutine(result):
                            await result
                    else:
                        result = self._client.light_command(
                            key=entity.key, state=state
                        )
                        if asyncio.iscoroutine(result):
                            await result
                except Exception as exc:
                    log.error('[%s] Command failed: %s', self.host, exc)

            asyncio.run_coroutine_threadsafe(_send(), self._loop)
            return True  # Optimistic accept

        return cb

    def _make_brightness_cb(self, entity):
        """Return an onchangecallback that sets light brightness (0-100)."""
        def cb(path, value):
            if self._client is None:
                return False
            brightness = max(0.0, min(1.0, float(value) / 100.0))

            async def _send():
                try:
                    result = self._client.light_command(
                        key=entity.key,
                        state=brightness > 0,
                        brightness=brightness,
                    )
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:
                    log.error('[%s] Brightness command failed: %s', self.host, exc)

            asyncio.run_coroutine_threadsafe(_send(), self._loop)
            return True

        return cb

    def _make_number_cb(self, entity):
        """Return an onchangecallback that sets a NumberInfo value on ESPHome."""
        def cb(path, value):
            if self._client is None:
                return False

            async def _send():
                try:
                    result = self._client.number_command(
                        key=entity.key, state=float(value)
                    )
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:
                    log.error('[%s] Number command failed: %s', self.host, exc)

            asyncio.run_coroutine_threadsafe(_send(), self._loop)
            return True

        return cb

    # ── Helpers ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _dbus_set(svc, path, value):
        """GLib idle callback: set one dbus path value."""
        svc[path] = value
        return False  # GLib.SOURCE_REMOVE

    def _set_connected(self, connected: bool):
        """GLib idle callback: mark all services connected/disconnected."""
        val = 1 if connected else 0
        if self._relay_svc:
            self._relay_svc['/Connected'] = val
        for svc in self._temp_svcs.values():
            svc['/Connected'] = val
        for svc, _ in self._tank_svcs.values():
            svc['/Connected'] = val
        return False


# ─────────────────────────────────────────────────────────────────────────────────
def parse_config(path: str) -> tuple[list[dict], dict]:
    """Return (list-of-device-dicts, sensor_ranges-dict) from config.ini."""
    cfg = configparser.ConfigParser()
    cfg.read(path)

    devices = []
    for section in cfg.sections():
        if section.lower().startswith('device'):
            devices.append({
                'host':           cfg[section]['host'].strip(),
                'encryption_key': cfg[section].get('encryption_key', '').strip(),
            })

    sensor_ranges = {}
    if 'sensor_ranges' in cfg:
        for raw_key, raw_val in cfg['sensor_ranges'].items():
            # Strip trailing comments and the ".max" suffix used in examples
            clean_val = raw_val.split('#')[0].strip()
            name = raw_key.rsplit('.', 1)[0] if raw_key.endswith('.max') else raw_key
            try:
                sensor_ranges[name] = float(clean_val)
            except ValueError:
                log.warning('sensor_ranges: cannot parse %s = %s', raw_key, raw_val)

    return devices, sensor_ranges


def run_asyncio(loop: asyncio.AbstractEventLoop, connections: list):
    """Background thread: run the asyncio event loop until stop."""
    asyncio.set_event_loop(loop)

    async def _all():
        await asyncio.gather(*[c.run() for c in connections])

    loop.run_until_complete(_all())


def main():
    import argparse

    parser = argparse.ArgumentParser(description='ESPHome → Venus OS dbus bridge')
    parser.add_argument(
        '--config',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini'),
        help='Path to config.ini (default: %(default)s)',
    )
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # GLib main loop must be set as dbus default before any dbus/threading activity
    DBusGMainLoop(set_as_default=True)

    devices, sensor_ranges = parse_config(args.config)
    if not devices:
        log.error('No [device_N] sections found in %s', args.config)
        sys.exit(1)

    log.info('Starting dbus-esphome with %d device(s)', len(devices))

    async_loop = asyncio.new_event_loop()
    connections = [
        DeviceConnection(
            host=d['host'],
            encryption_key=d['encryption_key'],
            sensor_ranges=sensor_ranges,
            async_loop=async_loop,
            device_idx=i,
        )
        for i, d in enumerate(devices)
    ]

    t = threading.Thread(
        target=run_asyncio, args=(async_loop, connections), daemon=True
    )
    t.start()

    glib_loop = GLib.MainLoop()
    try:
        glib_loop.run()
    except KeyboardInterrupt:
        log.info('Interrupted, shutting down …')
    finally:
        async_loop.call_soon_threadsafe(async_loop.stop)
        t.join(timeout=5)


if __name__ == '__main__':
    main()
