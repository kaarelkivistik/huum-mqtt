"""Entry point: wires the TCP controller link to the MQTT bridge."""

from __future__ import annotations

import asyncio
import logging
import os

from . import protocol as p
from .mqtt_bridge import HuumMqttBridge
from .state import DeviceState
from .tcp_server import HuumTcpServer

_LOGGER = logging.getLogger("huum")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def wire(
    tcp: HuumTcpServer,
    bridge: HuumMqttBridge,
    state: DeviceState,
    desired: dict,
    offline_grace: float = 0,
) -> None:
    """Connect controller events to state+MQTT, and MQTT commands to control.

    The controller drops and re-opens its TCP connection periodically (~40 min);
    `offline_grace` seconds of absence are tolerated before HA sees it offline.
    """
    pending_offline: dict[str, asyncio.Task] = {}

    def cancel_pending_offline() -> None:
        task = pending_offline.pop("task", None)
        if task is not None:
            task.cancel()

    # ---- controller -> state -> MQTT ------------------------------------- #
    async def on_handshake(hs: p.Handshake) -> None:
        cancel_pending_offline()
        state.apply_handshake(hs)
        await bridge.publish_discovery()
        await bridge.publish_availability(True)
        await bridge.publish_state()

    async def on_sensor(r: p.SensorReading) -> None:
        state.apply_sensor(r)
        await bridge.publish_state()

    async def on_state_report(s: p.StateReport) -> None:
        state.apply_state_report(s)
        if s.target_temperature:
            desired["target"] = s.target_temperature
        await bridge.publish_state()

    async def go_offline() -> None:
        if offline_grace:
            await asyncio.sleep(offline_grace)
        pending_offline.pop("task", None)
        _LOGGER.warning("Controller offline for %ss; marking unavailable",
                        offline_grace)
        await bridge.publish_availability(False)

    async def on_disconnect() -> None:
        state.set_connected(False)
        cancel_pending_offline()
        pending_offline["task"] = asyncio.create_task(go_offline())

    tcp.on_handshake = on_handshake
    tcp.on_sensor = on_sensor
    tcp.on_state_report = on_state_report
    tcp.on_disconnect = on_disconnect

    # ---- MQTT commands -> controller ------------------------------------- #
    async def on_set_mode(mode: str) -> None:
        if mode == "heat":
            await tcp.send_heater_on(desired["target"], bridge.duration_hours)
        elif mode == "off":
            await tcp.send_heater_off(desired["target"])
        else:
            _LOGGER.warning("Unknown mode command: %s", mode)

    async def on_set_temperature(temp: float) -> None:
        desired["target"] = int(round(temp))
        # Reflect the setpoint in HA immediately, even while off.
        state.target_temperature = desired["target"]
        # Re-arm with the new setpoint if we're currently heating.
        if state.heating:
            await tcp.send_heater_on(desired["target"], bridge.duration_hours)
        await bridge.publish_state()

    bridge.on_set_mode = on_set_mode
    bridge.on_set_temperature = on_set_temperature


async def _run() -> None:
    ping_frequency = _env_int("UPDATE_FREQUENCY", 60)
    min_temp = _env_int("MIN_TEMP", 40)
    max_temp = _env_int("MAX_TEMP", 110)
    default_target = _env_int("DEFAULT_TARGET", 70)
    default_duration = _env_int("DEFAULT_DURATION_HOURS", 3)
    max_duration = _env_int("MAX_DURATION_HOURS", 12)
    offline_grace = _env_int("OFFLINE_GRACE_SECONDS", 120)

    state = DeviceState()
    desired = {"target": default_target}

    tcp = HuumTcpServer(ping_frequency=ping_frequency)
    bridge = HuumMqttBridge(
        state,
        host=os.environ.get("MQTT_HOST", "core-mosquitto"),
        port=_env_int("MQTT_PORT", 1883),
        username=os.environ.get("MQTT_USERNAME") or None,
        password=os.environ.get("MQTT_PASSWORD") or None,
        min_temp=min_temp,
        max_temp=max_temp,
        max_duration_hours=max_duration,
        default_duration_hours=default_duration,
    )

    wire(tcp, bridge, state, desired, offline_grace=offline_grace)

    # ---- run both, restart the MQTT side on connection loss -------------- #
    async def mqtt_forever() -> None:
        while True:
            try:
                await bridge.run()
            except Exception as err:  # noqa: BLE001 - keep the add-on alive
                _LOGGER.warning("MQTT connection lost (%s); retrying in 5s", err)
                await asyncio.sleep(5)

    await tcp.start()
    await asyncio.gather(tcp.serve_forever(), mqtt_forever())


def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("DEBUG") == "1" else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
