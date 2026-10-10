"""Huum sauna controller wire protocol.

Pure encode/decode functions for the controller's local TCP protocol on port
6969, reverse-engineered from kpalang/huum-controller plus our own packet
captures (see PLAN.md). No I/O here so it can be unit-tested against captured
payloads.

All multi-byte timestamps are little-endian uint32 unix seconds.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum


class MessageType(IntEnum):
    CONFIGURATION = 0x02        # we -> heater: set ping frequency
    LOCAL_HEATER_CONTROL = 0x07  # control command / echo (on/off)
    CLOUD_UPDATE = 0x08         # heater -> us: authoritative state report
    SENSOR_READING = 0x09       # heater -> us: periodic temp/status ping
    TELEMETRY_0E = 0x0E         # heater -> us: constant telemetry, purpose TBD
    HANDSHAKE = 0x0B            # heater -> us: identity on connect


# Trailing constant blocks kpalang found; accepted by our firmware. Keep as-is.
_ON_TAIL = bytes([0xEC, 0xC5, 0xEF, 0x10])
_OFF_TAIL = bytes([0x75, 0x59, 0xFC, 0x10])
_MODE = 0x03  # byte[6] "mode"; constant in every control/state frame we've seen

# Fixed on-the-wire length of each message type. The protocol has no length
# prefix or delimiter, so framing relies on these. Multiple messages can arrive
# coalesced in a single TCP read (e.g. a 0x09 followed by a 0x0E).
MESSAGE_LENGTHS = {
    MessageType.CONFIGURATION: 7,
    MessageType.LOCAL_HEATER_CONTROL: 24,
    MessageType.CLOUD_UPDATE: 25,
    MessageType.SENSOR_READING: 11,
    MessageType.TELEMETRY_0E: 13,
    MessageType.HANDSHAKE: 77,
}


class UnknownMessageError(ValueError):
    """Raised when a buffer starts with an unrecognised message type, so its
    length is unknown and the stream can no longer be framed safely."""

    def __init__(self, remaining: bytes):
        self.remaining = remaining
        super().__init__(f"unknown message type 0x{remaining[0]:02x}: {remaining.hex()}")


def split_frames(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split a byte buffer into complete protocol frames.

    Returns (frames, remaining) where `remaining` is a trailing partial frame to
    be prepended to the next read. Raises UnknownMessageError if the buffer head
    is an unrecognised type (unframeable).
    """
    frames: list[bytes] = []
    offset = 0
    n = len(buf)
    while offset < n:
        msg_type = buf[offset]
        length = MESSAGE_LENGTHS.get(msg_type)
        if length is None:
            raise UnknownMessageError(bytes(buf[offset:]))
        if offset + length > n:
            break  # incomplete trailing frame; wait for more bytes
        frames.append(bytes(buf[offset:offset + length]))
        offset += length
    return frames, bytes(buf[offset:])


# --------------------------------------------------------------------------- #
# Parsed message dataclasses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Handshake:
    mac_address: str
    firmware: str
    device_name: str


@dataclass(frozen=True)
class SensorReading:
    temperature: int          # degrees C
    frequency_seconds: int    # heater's current ping interval
    status_byte: int          # byte[4]; 0x26 == online on our firmware
    flag_byte: int            # byte[5]; 0x02 idle / 0x00 heating (unconfirmed)


@dataclass(frozen=True)
class StateReport:
    """0x08 - the authoritative heating-state report."""
    target_temperature: int
    heating_started_at: datetime | None
    heating_ends_at: datetime | None
    reported_at: datetime | None

    @property
    def heating(self) -> bool:
        return self.heating_started_at is not None


@dataclass(frozen=True)
class ControlEcho:
    """0x07 - command we sent, or the heater's echo of it."""
    target_temperature: int
    reported_at: datetime | None


# --------------------------------------------------------------------------- #
# Timestamp helpers
# --------------------------------------------------------------------------- #
def _ts_to_le(dt: datetime) -> bytes:
    return struct.pack("<I", int(dt.timestamp()))


def _le_to_ts(buf: bytes) -> datetime | None:
    value = struct.unpack("<I", buf)[0]
    if value == 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


# --------------------------------------------------------------------------- #
# Decoding
# --------------------------------------------------------------------------- #
_FW_OFFSET = 32
_NAME_OFFSET = 62


def _cstr(payload: bytes, start: int) -> str:
    """Read a null-terminated ascii string starting at `start`."""
    end = payload.find(b"\x00", start)
    if end < 0:
        end = len(payload)
    return payload[start:end].decode("utf-8", "replace").strip()


def parse_handshake(payload: bytes) -> Handshake:
    mac = ":".join(f"{b:02x}" for b in reversed(payload[1:7]))
    # Parse as null-terminated (upstream used fixed slices and truncated the
    # name, e.g. "UQ4.2 EU WiFi" -> "UQ4.2 EU Wi").
    firmware = _cstr(payload, _FW_OFFSET)
    name = _cstr(payload, _NAME_OFFSET)
    return Handshake(mac_address=mac, firmware=firmware, device_name=name)


def parse_sensor_reading(payload: bytes) -> SensorReading:
    return SensorReading(
        temperature=payload[1],
        frequency_seconds=payload[3],
        status_byte=payload[4],
        flag_byte=payload[5],
    )


def parse_state_report(payload: bytes) -> StateReport:
    return StateReport(
        target_temperature=payload[1],
        heating_started_at=_le_to_ts(payload[7:11]),
        heating_ends_at=_le_to_ts(payload[11:15]),
        reported_at=_le_to_ts(payload[15:19]),
    )


def parse_control(payload: bytes) -> ControlEcho:
    # In an ON command, offsets 7/11/15 hold start/end/start timestamps; in an
    # OFF command only offset 15 is set. We surface the one at 15 as reported_at.
    return ControlEcho(
        target_temperature=payload[1],
        reported_at=_le_to_ts(payload[15:19]),
    )


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #
def build_frequency_update(delay_seconds: int, now: datetime | None = None) -> bytes:
    if not 0 <= delay_seconds <= 255:
        raise ValueError(f"delay must be 0..255, got {delay_seconds}")
    now = now or datetime.now(tz=timezone.utc)
    buf = bytearray(7)
    buf[0] = MessageType.CONFIGURATION
    buf[1:5] = _ts_to_le(now)
    buf[5] = delay_seconds
    return bytes(buf)


def build_heater_on(
    target_temp: int,
    duration_hours: float,
    now: datetime | None = None,
) -> bytes:
    now = now or datetime.now(tz=timezone.utc)
    end = datetime.fromtimestamp(
        int(now.timestamp()) + int(round(duration_hours * 3600)), tz=timezone.utc
    )
    buf = bytearray(24)
    buf[0] = MessageType.LOCAL_HEATER_CONTROL
    buf[1] = target_temp
    buf[6] = _MODE
    buf[7:11] = _ts_to_le(now)
    buf[11:15] = _ts_to_le(end)
    buf[15:19] = _ts_to_le(now)
    buf[19:23] = _ON_TAIL
    return bytes(buf)


def build_heater_off(target_temp: int, now: datetime | None = None) -> bytes:
    now = now or datetime.now(tz=timezone.utc)
    buf = bytearray(24)
    buf[0] = MessageType.LOCAL_HEATER_CONTROL
    buf[1] = target_temp
    buf[6] = _MODE
    buf[15:19] = _ts_to_le(now)
    buf[19:23] = _OFF_TAIL
    return bytes(buf)
