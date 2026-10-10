"""In-memory model of the sauna's current state.

Fed by the TCP layer (handshake / sensor / state-report messages) and read by
the MQTT bridge. Heating on/off is authoritative from the 0x08 state report; the
0x09 flag byte is used only as a provisional hint before the first 0x08 arrives
(e.g. to recover state after an add-on restart mid-session).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import protocol as p

# 0x09 byte[5]: 0x00 seen while heating, 0x02 while idle (single-sample
# hypothesis, used only as a pre-0x08 hint).
_FLAG_HEATING = 0x00
_FLAG_IDLE = 0x02


@dataclass
class DeviceState:
    mac_address: str | None = None
    firmware: str | None = None
    device_name: str | None = None

    connected: bool = False
    current_temperature: int | None = None
    target_temperature: int | None = None
    heating: bool | None = None
    heating_ends_at: datetime | None = None

    # True once a 0x08 has set `heating`; until then we may use the sensor hint.
    _state_report_seen: bool = field(default=False, repr=False)

    # ---- updates from the wire ------------------------------------------- #
    def apply_handshake(self, hs: p.Handshake) -> None:
        self.mac_address = hs.mac_address
        self.firmware = hs.firmware
        self.device_name = hs.device_name
        self.connected = True

    def apply_sensor(self, r: p.SensorReading) -> None:
        self.current_temperature = r.temperature
        if not self._state_report_seen:
            if r.flag_byte == _FLAG_HEATING:
                self.heating = True
            elif r.flag_byte == _FLAG_IDLE:
                self.heating = False

    def apply_state_report(self, s: p.StateReport) -> None:
        self._state_report_seen = True
        self.target_temperature = s.target_temperature
        self.heating = s.heating
        self.heating_ends_at = s.heating_ends_at

    def set_connected(self, value: bool) -> None:
        self.connected = value
