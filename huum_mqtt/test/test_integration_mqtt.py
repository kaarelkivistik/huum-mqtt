"""End-to-end test: Mosquitto + bridge + fake controller + an 'HA' MQTT client.

Skipped automatically if `mosquitto` or `aiomqtt` is unavailable.

Verifies:
  - the climate entity is announced via MQTT Discovery (retained)
  - a `mode/set heat` command drives the controller and the reported state
    flips to heat / action heating
  - a `mode/set off` command turns it back off
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAVE_MOSQUITTO = shutil.which("mosquitto") is not None
try:
    import aiomqtt  # noqa: F401
    HAVE_AIOMQTT = True
except ImportError:
    HAVE_AIOMQTT = False

if HAVE_AIOMQTT:
    from huum.main import wire
    from huum.mqtt_bridge import HuumMqttBridge
    from huum.state import DeviceState
    from huum.tcp_server import HuumTcpServer
    from tools.fake_controller import FakeController

NODE = "huum_0cea4cdaf380"  # from the captured handshake MAC


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"port {port} never opened")


@unittest.skipUnless(HAVE_MOSQUITTO and HAVE_AIOMQTT, "needs mosquitto + aiomqtt")
class TestEndToEnd(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mqtt_port = _free_port()
        self._conf = tempfile.NamedTemporaryFile(
            "w", suffix=".conf", delete=False
        )
        self._conf.write(
            f"listener {self.mqtt_port} 127.0.0.1\nallow_anonymous true\n"
        )
        self._conf.close()
        self._broker = subprocess.Popen(
            ["mosquitto", "-c", self._conf.name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _wait_port(self.mqtt_port)

    async def asyncTearDown(self):
        self._broker.terminate()
        self._broker.wait(timeout=5)
        os.unlink(self._conf.name)

    async def _collect(self, client, want_topic, expected=None, timeout=5.0):
        """Return a payload on `want_topic`. If `expected` is given, skip
        messages until one matches (the controller republishes state on every
        tick, so there can be a backlog of stale values)."""
        async def _loop():
            async for msg in client.messages:
                if str(msg.topic) == want_topic:
                    payload = msg.payload.decode()
                    if expected is None or payload == expected:
                        return payload
        return await asyncio.wait_for(_loop(), timeout)

    async def test_discovery_and_control(self):
        state = DeviceState()
        tcp = HuumTcpServer(ping_frequency=5, host="127.0.0.1", port=0)
        bridge = HuumMqttBridge(state, host="127.0.0.1", port=self.mqtt_port)
        wire(tcp, bridge, state, {"target": 70})

        await tcp.start()
        bridge_task = asyncio.create_task(bridge.run())
        await asyncio.sleep(0.5)  # let the bridge connect & subscribe

        controller = FakeController("127.0.0.1", tcp.port, tick_seconds=0.5)
        ctrl_task = asyncio.create_task(controller.run())

        try:
            async with aiomqtt.Client("127.0.0.1", port=self.mqtt_port) as ha:
                await ha.subscribe("homeassistant/#")
                await ha.subscribe(f"huum/{NODE}/#")

                # 1. Discovery: the climate config is published (retained).
                cfg = await self._collect(
                    ha, f"homeassistant/climate/{NODE}/climate/config"
                )
                cfg = json.loads(cfg)
                self.assertEqual(cfg["modes"], ["off", "heat"])
                self.assertIn("temperature_command_topic", cfg)

                # 2. Turn on -> controller heats -> state reports heat/heating.
                await ha.publish(f"huum/{NODE}/mode/set", "heat")
                self.assertEqual(
                    await self._collect(ha, f"huum/{NODE}/mode/state", "heat"),
                    "heat",
                )
                self.assertEqual(
                    await self._collect(
                        ha, f"huum/{NODE}/action/state", "heating"
                    ),
                    "heating",
                )

                # 3. Turn off -> state reports off.
                await ha.publish(f"huum/{NODE}/mode/set", "off")
                self.assertEqual(
                    await self._collect(ha, f"huum/{NODE}/mode/state", "off"),
                    "off",
                )
        finally:
            ctrl_task.cancel()
            bridge_task.cancel()
            await controller.close()


if __name__ == "__main__":
    unittest.main()
