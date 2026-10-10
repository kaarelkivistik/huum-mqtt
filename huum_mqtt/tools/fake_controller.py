"""A fake Huum controller for local testing.

Connects to the add-on's TCP server (default 127.0.0.1:6969), sends the real
captured handshake, then emulates the device: periodic 0x09 sensor readings, and
0x08 state reports in response to 0x07 on/off commands. Temperature drifts
toward the target while heating.

Standalone:
    python tools/fake_controller.py [host] [port]

Also importable as FakeController for tests.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import sys
import time

_LOGGER = logging.getLogger("fake_controller")

# Real captured handshake (firmware UQ4-4.4.18.0-5c1a4c75-16, UQ4.2 EU WiFi).
HANDSHAKE = bytes.fromhex(
    "0b80f3da4cea0c0000000000000000000000000000000000000000000000"
    "00005551342d342e342e31382e302d35633161346337352d313600000000"
    "00005551342e3220455520576946690000"
)


def _le(ts: int) -> bytes:
    return struct.pack("<I", ts)


class FakeController:
    def __init__(self, host: str = "127.0.0.1", port: int = 6969,
                 tick_seconds: float = 2.0) -> None:
        self._host = host
        self._port = port
        self._tick = tick_seconds
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.heating = False
        self.target = 70
        self.temperature = 23
        self.heating_ends = 0

    def _sensor(self) -> bytes:
        flag = 0x00 if self.heating else 0x02  # byte[5] hypothesis
        return bytes([0x09, self.temperature & 0xFF, 0x00, 0x3C, 0x26, flag,
                      0, 0, 0, 0, 0])

    def _state(self) -> bytes:
        now = int(time.time())
        start = now if self.heating else 0
        end = self.heating_ends if self.heating else 0
        buf = bytearray(25)
        buf[0] = 0x08
        buf[1] = self.target & 0xFF
        buf[6] = 0x03
        buf[7:11] = _le(start)
        buf[11:15] = _le(end)
        buf[15:19] = _le(now)
        buf[23] = 0x01
        return bytes(buf)

    async def _send(self, data: bytes, label: str) -> None:
        assert self._writer is not None
        _LOGGER.info("TX %-10s %s", label, data.hex())
        self._writer.write(data)
        await self._writer.drain()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )
        await self._send(HANDSHAKE, "handshake")

    async def _handle_command(self, frame: bytes) -> None:
        if frame[0] == 0x02:
            _LOGGER.info("RX freq-update interval=%ds", frame[5])
            return
        if frame[0] != 0x07:
            _LOGGER.info("RX other %s", frame.hex())
            return
        self.target = frame[1]
        turn_on = any(frame[7:11])  # ON carries a start timestamp
        self.heating = turn_on
        if turn_on:
            self.heating_ends = int(time.time()) + 3600
            _LOGGER.info("RX ON  target=%d", self.target)
        else:
            _LOGGER.info("RX OFF target=%d", self.target)
        await self._send(self._state(), "state")
        await self._send(self._sensor(), "sensor")

    async def _reader_loop(self) -> None:
        from huum import protocol as p  # local import for standalone use
        buf = b""
        assert self._reader is not None
        while True:
            chunk = await self._reader.read(4096)
            if not chunk:
                break
            buf += chunk
            try:
                frames, buf = p.split_frames(buf)
            except p.UnknownMessageError:
                buf = b""
                continue
            for frame in frames:
                await self._handle_command(frame)

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(self._tick)
            if self.heating and self.temperature < self.target:
                self.temperature += 1
            elif not self.heating and self.temperature > 20:
                self.temperature -= 1
            await self._send(self._sensor(), "sensor")

    async def run(self) -> None:
        await self.connect()
        await asyncio.gather(self._reader_loop(), self._ticker())

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()


async def _main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 6969
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    controller = FakeController(host, port)
    try:
        await controller.run()
    except (ConnectionError, asyncio.IncompleteReadError) as err:
        _LOGGER.error("Connection ended: %s", err)


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.run(_main())
