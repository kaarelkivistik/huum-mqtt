"""Protocol tests against real captured payloads (see PLAN.md).

Run: python3 -m unittest discover -s test   (from the huum-mqtt directory)
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from huum import protocol as p  # noqa: E402


def hx(s: str) -> bytes:
    return bytes.fromhex(s)


# Captured payloads -----------------------------------------------------------
HANDSHAKE = hx(
    "0b80f3da4cea0c0000000000000000000000000000000000000000000000"
    "00005551342d342e342e31382e302d35633161346337352d313600000000"
    "00005551342e3220455520576946690000"
)
SENSOR_IDLE = hx("0917003c26020000000000")     # temp 23, freq 60, idle
SENSOR_HEATING = hx("0917003c26000000000000")  # temp 23, freq 60, heating
STATE_START = hx("084700000000034638c96a5646c96a4738c96a000000000100")
STATE_STOP = hx("0847000000000300000000000000008538c96a000000000100")
CTRL_ON_SENT = hx("074700000000035a37c96a6a45c96a5a37c96aecc5ef1000")
CTRL_OFF_SENT = hx("0747000000000300000000000000008238c96a7559fc1000")
CFG_SENT = hx("02f8fec86a3c00")


class TestDecode(unittest.TestCase):
    def test_handshake(self):
        hs = p.parse_handshake(HANDSHAKE)
        self.assertEqual(hs.mac_address, "0c:ea:4c:da:f3:80")
        self.assertEqual(hs.firmware, "UQ4-4.4.18.0-5c1a4c75-16")
        self.assertEqual(hs.device_name, "UQ4.2 EU WiFi")  # full, not truncated

    def test_sensor_idle(self):
        r = p.parse_sensor_reading(SENSOR_IDLE)
        self.assertEqual(r.temperature, 23)
        self.assertEqual(r.frequency_seconds, 60)
        self.assertEqual(r.status_byte, 0x26)
        self.assertEqual(r.flag_byte, 0x02)

    def test_sensor_heating(self):
        r = p.parse_sensor_reading(SENSOR_HEATING)
        self.assertEqual(r.temperature, 23)
        self.assertEqual(r.flag_byte, 0x00)

    def test_state_start_means_heating(self):
        s = p.parse_state_report(STATE_START)
        self.assertEqual(s.target_temperature, 71)
        self.assertTrue(s.heating)
        self.assertIsNotNone(s.heating_started_at)
        self.assertIsNotNone(s.heating_ends_at)
        # end is one hour after start (duration was 1h)
        delta = s.heating_ends_at - s.heating_started_at
        self.assertEqual(delta.total_seconds(), 3600)

    def test_state_stop_means_off(self):
        s = p.parse_state_report(STATE_STOP)
        self.assertFalse(s.heating)
        self.assertIsNone(s.heating_started_at)
        self.assertIsNone(s.heating_ends_at)
        self.assertIsNotNone(s.reported_at)


class TestEncode(unittest.TestCase):
    def test_build_heater_on_matches_capture(self):
        # Reconstruct the exact ON frame we sent (start ts from the capture).
        start = p._le_to_ts(hx("5a37c96a"))
        out = p.build_heater_on(71, duration_hours=1, now=start)
        self.assertEqual(out.hex(), CTRL_ON_SENT.hex())

    def test_build_heater_off_matches_capture(self):
        ts = p._le_to_ts(hx("8238c96a"))
        out = p.build_heater_off(71, now=ts)
        self.assertEqual(out.hex(), CTRL_OFF_SENT.hex())

    def test_build_frequency_update_matches_capture(self):
        ts = p._le_to_ts(hx("f8fec86a"))
        out = p.build_frequency_update(60, now=ts)
        self.assertEqual(out.hex(), CFG_SENT.hex())

    def test_frequency_bounds(self):
        with self.assertRaises(ValueError):
            p.build_frequency_update(256)

    def test_on_then_parse_roundtrip(self):
        now = datetime(2026, 10, 9, 18, 0, 0, tzinfo=timezone.utc)
        frame = p.build_heater_on(80, 2, now=now)
        echo = p.parse_control(frame)
        self.assertEqual(echo.target_temperature, 80)


class TestFraming(unittest.TestCase):
    def test_single_frame(self):
        frames, rest = p.split_frames(SENSOR_IDLE)
        self.assertEqual(len(frames), 1)
        self.assertEqual(rest, b"")

    def test_coalesced_sensor_plus_telemetry(self):
        # The real first-boot segment: an 11-byte 0x09 glued to a 13-byte 0x0E.
        coalesced = hx("09170001260200000000000ee8ffffff7d0000001c000000")
        self.assertEqual(len(coalesced), 24)
        frames, rest = p.split_frames(coalesced)
        self.assertEqual([f[0] for f in frames], [0x09, 0x0E])
        self.assertEqual(rest, b"")

    def test_partial_trailing_frame_is_kept(self):
        # A full 0x09 plus the first 3 bytes of a 0x08.
        buf = SENSOR_IDLE + STATE_START[:3]
        frames, rest = p.split_frames(buf)
        self.assertEqual(len(frames), 1)
        self.assertEqual(rest, STATE_START[:3])
        # Completing it on the next read yields the 0x08.
        frames2, rest2 = p.split_frames(rest + STATE_START[3:])
        self.assertEqual(len(frames2), 1)
        self.assertEqual(frames2[0][0], 0x08)
        self.assertEqual(rest2, b"")

    def test_unknown_type_raises(self):
        with self.assertRaises(p.UnknownMessageError):
            p.split_frames(bytes([0xFF, 0x01, 0x02]))


if __name__ == "__main__":
    unittest.main()
