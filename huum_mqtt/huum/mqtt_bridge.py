"""MQTT bridge: publishes the sauna as an HA climate entity via MQTT Discovery
and turns HA commands into controller actions.

Entities (keyed on the controller MAC for a stable device):
  - climate: off/heat, target + current temperature, action (heating/idle/off)
  - number:  session duration in hours (HUUM needs a duration on start)
  - sensor:  session end time (when heating)
"""

from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional

import aiomqtt

from .state import DeviceState

_LOGGER = logging.getLogger(__name__)

ONLINE = "online"
OFFLINE = "offline"

Callback = Callable[..., Optional[Awaitable[None]]]


class HuumMqttBridge:
    def __init__(
        self,
        state: DeviceState,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        *,
        min_temp: int = 40,
        max_temp: int = 110,
        max_duration_hours: int = 12,
        default_duration_hours: int = 3,
        discovery_prefix: str = "homeassistant",
    ) -> None:
        self._state = state
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._min_temp = min_temp
        self._max_temp = max_temp
        self._max_duration = max_duration_hours
        self.duration_hours = default_duration_hours
        self._discovery_prefix = discovery_prefix

        self._client: aiomqtt.Client | None = None
        self._discovery_sent = False

        # Wired by main.py
        self.on_set_mode: Callback | None = None          # (mode: "heat"|"off")
        self.on_set_temperature: Callback | None = None   # (temp: float)
        self.on_set_duration: Callback | None = None      # (hours: float)

    # ---- topics ---------------------------------------------------------- #
    @property
    def _node_id(self) -> str:
        mac = (self._state.mac_address or "unknown").replace(":", "")
        return f"huum_{mac}"

    @property
    def _base(self) -> str:
        return f"huum/{self._node_id}"

    @property
    def _availability_topic(self) -> str:
        return f"{self._base}/availability"

    # ---- lifecycle ------------------------------------------------------- #
    async def run(self) -> None:
        will = aiomqtt.Will(
            self._availability_topic, OFFLINE, qos=1, retain=True
        )
        async with aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            will=will,
        ) as client:
            self._client = client
            _LOGGER.info("Connected to MQTT %s:%d", self._host, self._port)
            # Node-wildcard so the subscription is valid regardless of when the
            # MAC (and thus the node id) becomes known at handshake time.
            await client.subscribe("huum/+/+/set")
            if self._state.mac_address:
                await self.publish_discovery()
            await self.publish_availability(self._state.connected)
            await self.publish_state()
            try:
                async for message in client.messages:
                    await self._handle_command(message)
            finally:
                self._client = None

    async def _publish(self, topic: str, payload, retain: bool = False) -> None:
        client = self._client
        if client is None:
            return
        if not isinstance(payload, (str, bytes)):
            payload = json.dumps(payload)
        try:
            await client.publish(topic, payload, qos=1, retain=retain)
        except aiomqtt.MqttError as err:
            # Connection dropped between callback and publish; the reconnect
            # loop will re-establish and re-publish current state.
            _LOGGER.debug("Dropped publish to %s (%s)", topic, err)

    # ---- discovery ------------------------------------------------------- #
    async def publish_discovery(self) -> None:
        device = {
            "identifiers": [self._node_id],
            "manufacturer": "HUUM",
            "model": self._state.device_name or "Uku WiFi",
            "name": "Sauna",
            "sw_version": self._state.firmware,
        }
        avail = [{"topic": self._availability_topic,
                  "payload_available": ONLINE,
                  "payload_not_available": OFFLINE}]

        climate = {
            "name": None,  # use the device name
            "unique_id": f"{self._node_id}_climate",
            "device": device,
            "availability": avail,
            "modes": ["off", "heat"],
            "mode_command_topic": f"{self._base}/mode/set",
            "mode_state_topic": f"{self._base}/mode/state",
            "temperature_command_topic": f"{self._base}/target/set",
            "temperature_state_topic": f"{self._base}/target/state",
            "current_temperature_topic": f"{self._base}/current/state",
            "action_topic": f"{self._base}/action/state",
            "min_temp": self._min_temp,
            "max_temp": self._max_temp,
            "temp_step": 1,
            "temperature_unit": "C",
        }
        number = {
            "name": "Session duration",
            "unique_id": f"{self._node_id}_duration",
            "device": device,
            "availability": avail,
            "command_topic": f"{self._base}/duration/set",
            "state_topic": f"{self._base}/duration/state",
            "min": 1,
            "max": self._max_duration,
            "step": 1,
            "unit_of_measurement": "h",
            "icon": "mdi:timer",
            "entity_category": "config",
        }
        ends = {
            "name": "Session ends at",
            "unique_id": f"{self._node_id}_ends_at",
            "device": device,
            "availability": avail,
            "state_topic": f"{self._base}/ends_at/state",
            "device_class": "timestamp",
            "icon": "mdi:timer-sand",
        }

        pfx = self._discovery_prefix
        await self._publish(f"{pfx}/climate/{self._node_id}/climate/config",
                            climate, retain=True)
        await self._publish(f"{pfx}/number/{self._node_id}/duration/config",
                            number, retain=True)
        await self._publish(f"{pfx}/sensor/{self._node_id}/ends_at/config",
                            ends, retain=True)
        self._discovery_sent = True
        await self._publish(f"{self._base}/duration/state",
                            str(self.duration_hours), retain=True)
        _LOGGER.info("Published MQTT discovery for %s", self._node_id)

    # ---- state out ------------------------------------------------------- #
    async def publish_state(self) -> None:
        s = self._state
        if s.current_temperature is not None:
            await self._publish(f"{self._base}/current/state",
                                str(s.current_temperature), retain=True)
        if s.target_temperature is not None:
            await self._publish(f"{self._base}/target/state",
                                str(s.target_temperature), retain=True)
        if s.heating is not None:
            await self._publish(f"{self._base}/mode/state",
                                "heat" if s.heating else "off", retain=True)
            await self._publish(f"{self._base}/action/state",
                                "heating" if s.heating else "off", retain=True)
        await self._publish(
            f"{self._base}/ends_at/state",
            s.heating_ends_at.isoformat() if s.heating_ends_at else "None",
            retain=True,
        )

    async def publish_availability(self, online: bool) -> None:
        await self._publish(self._availability_topic,
                            ONLINE if online else OFFLINE, retain=True)

    # ---- commands in ----------------------------------------------------- #
    async def _handle_command(self, message: aiomqtt.Message) -> None:
        topic = str(message.topic)
        payload = message.payload.decode() if message.payload else ""
        _LOGGER.debug("MQTT cmd %s = %s", topic, payload)
        try:
            if topic.endswith("/mode/set"):
                await self._fire(self.on_set_mode, payload.strip().lower())
            elif topic.endswith("/target/set"):
                await self._fire(self.on_set_temperature, float(payload))
            elif topic.endswith("/duration/set"):
                self.duration_hours = int(float(payload))
                await self._publish(f"{self._base}/duration/state",
                                    str(self.duration_hours), retain=True)
                await self._fire(self.on_set_duration, self.duration_hours)
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Bad command on %s: %s (%s)", topic, payload, err)

    @staticmethod
    async def _fire(cb: Callback | None, *args) -> None:
        if cb is None:
            return
        result = cb(*args)
        if hasattr(result, "__await__"):
            await result
