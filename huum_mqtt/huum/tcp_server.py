"""Asyncio TCP server that terminates the sauna controller's local protocol.

The controller opens one long-lived connection to port 6969 (after the DNS
redirect of api.huum.eu and the 8883 block force it onto the legacy plaintext
channel). This server holds that connection, frames and parses incoming
messages, dispatches them via callbacks, and lets callers push control frames.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

TCP_PORT = 6969  # fixed by the controller; do not change
Handler = Callable[..., Optional[Awaitable[None]]]


class HuumTcpServer:
    def __init__(
        self, ping_frequency: int = 60, host: str = "0.0.0.0", port: int = TCP_PORT
    ) -> None:
        self._host = host
        self._port = port
        self._ping_frequency = ping_frequency
        self._writer: asyncio.StreamWriter | None = None
        self._server: asyncio.Server | None = None

        # Callbacks wired by main.py. Any may be async.
        self.on_handshake: Handler | None = None
        self.on_sensor: Handler | None = None
        self.on_state_report: Handler | None = None
        self.on_control: Handler | None = None
        self.on_connect: Handler | None = None
        self.on_disconnect: Handler | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        if self._port == 0:
            self._port = self._server.sockets[0].getsockname()[1]
        _LOGGER.info("TCP server listening on %s:%d", self._host, self._port)

    @property
    def port(self) -> int:
        return self._port

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    # ---- outgoing commands ----------------------------------------------- #
    async def send_frequency(self, delay_seconds: int) -> None:
        await self._write(p.build_frequency_update(delay_seconds))

    async def send_heater_on(self, target_temp: int, duration_hours: float) -> None:
        await self._write(p.build_heater_on(target_temp, duration_hours))

    async def send_heater_off(self, target_temp: int) -> None:
        await self._write(p.build_heater_off(target_temp))

    async def _write(self, data: bytes) -> None:
        if not self.connected:
            _LOGGER.warning("No controller connected; dropping %s", data.hex())
            return
        assert self._writer is not None
        _LOGGER.debug("TX %s", data.hex())
        self._writer.write(data)
        await self._writer.drain()

    # ---- connection handling --------------------------------------------- #
    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        _LOGGER.info("Controller connected from %s", peer)
        if self._writer is not None and not self._writer.is_closing():
            _LOGGER.warning("Replacing existing controller connection")
            self._writer.close()
        self._writer = writer
        await self._fire(self.on_connect)

        buffer = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buffer += chunk
                try:
                    frames, buffer = p.split_frames(buffer)
                except p.UnknownMessageError as err:
                    _LOGGER.warning("Unframeable data, resetting buffer: %s", err)
                    buffer = b""
                    continue
                for frame in frames:
                    await self._dispatch(frame)
        except (ConnectionError, asyncio.IncompleteReadError) as err:
            _LOGGER.info("Controller connection error: %s", err)
        finally:
            writer.close()
            # A replaced connection's teardown must not mark the device offline;
            # the newer connection is already live.
            if self._writer is writer:
                self._writer = None
                _LOGGER.info("Controller disconnected")
                await self._fire(self.on_disconnect)
            else:
                _LOGGER.info("Replaced controller connection closed")

    async def _dispatch(self, frame: bytes) -> None:
        msg_type = frame[0]
        _LOGGER.debug("RX %s", frame.hex())
        if msg_type == p.MessageType.HANDSHAKE:
            hs = p.parse_handshake(frame)
            _LOGGER.info("Handshake: %s", hs)
            await self._fire(self.on_handshake, hs)
            # Prompt the controller to start reporting at our cadence.
            await self.send_frequency(self._ping_frequency)
        elif msg_type == p.MessageType.SENSOR_READING:
            await self._fire(self.on_sensor, p.parse_sensor_reading(frame))
        elif msg_type == p.MessageType.CLOUD_UPDATE:
            await self._fire(self.on_state_report, p.parse_state_report(frame))
        elif msg_type == p.MessageType.LOCAL_HEATER_CONTROL:
            await self._fire(self.on_control, p.parse_control(frame))
        elif msg_type == p.MessageType.TELEMETRY_0E:
            pass  # constant telemetry, purpose unknown; ignore for now
        else:
            _LOGGER.debug("Unhandled message 0x%02x: %s", msg_type, frame.hex())

    @staticmethod
    async def _fire(handler: Handler | None, *args) -> None:
        if handler is None:
            return
        result = handler(*args)
        if asyncio.iscoroutine(result):
            await result
