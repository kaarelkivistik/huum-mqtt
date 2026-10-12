"""Integration test: drive the asyncio TCP server with a fake controller.

Feeds the real captured handshake and a heating 0x08, and checks the server
parses them, fires callbacks, and replies to the handshake with a 0x02.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from huum.tcp_server import HuumTcpServer  # noqa: E402

HANDSHAKE = bytes.fromhex(
    "0b80f3da4cea0c0000000000000000000000000000000000000000000000"
    "00005551342d342e342e31382e302d35633161346337352d313600000000"
    "00005551342e3220455520576946690000"
)
STATE_START = bytes.fromhex("084700000000034638c96a5646c96a4738c96a000000000100")


class TestTcpServer(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_state(self):
        got = {}
        server = HuumTcpServer(ping_frequency=60, host="127.0.0.1", port=0)
        server.on_handshake = lambda hs: got.__setitem__("hs", hs)
        server.on_state_report = lambda s: got.__setitem__("state", s)
        await server.start()

        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        # Send handshake, then the heating state report, coalesced.
        writer.write(HANDSHAKE + STATE_START)
        await writer.drain()

        # Server should reply to the handshake with a 0x02 frequency update.
        reply = await asyncio.wait_for(reader.readexactly(7), timeout=2)
        self.assertEqual(reply[0], 0x02)
        self.assertEqual(reply[5], 60)  # our ping frequency

        await asyncio.sleep(0.05)  # let dispatch finish
        writer.close()

        self.assertEqual(got["hs"].device_name, "UQ4.2 EU WiFi")
        self.assertTrue(got["state"].heating)
        self.assertEqual(got["state"].target_temperature, 71)


    async def test_replaced_connection_does_not_fire_disconnect(self):
        disconnects = []
        server = HuumTcpServer(ping_frequency=60, host="127.0.0.1", port=0)
        server.on_disconnect = lambda: disconnects.append(1)
        await server.start()

        _, w1 = await asyncio.open_connection("127.0.0.1", server.port)
        await asyncio.sleep(0.05)
        _, w2 = await asyncio.open_connection("127.0.0.1", server.port)
        await asyncio.sleep(0.05)
        self.assertEqual(disconnects, [])  # old link torn down silently
        self.assertTrue(server.connected)

        w2.close()
        await asyncio.sleep(0.05)
        self.assertEqual(disconnects, [1])
        w1.close()

    async def test_offline_grace(self):
        from huum.main import wire
        from huum.state import DeviceState

        class FakeBridge:
            duration_hours = 3
            def __init__(self):
                self.avail = []
            async def publish_availability(self, online):
                self.avail.append(online)
            async def publish_discovery(self):
                pass
            async def publish_state(self):
                pass

        tcp = HuumTcpServer(ping_frequency=60, host="127.0.0.1", port=0)
        bridge = FakeBridge()
        wire(tcp, bridge, DeviceState(), {"target": 70}, offline_grace=0.2)
        await tcp.start()

        # Drop and reconnect within the grace period: never goes offline.
        r, w = await asyncio.open_connection("127.0.0.1", tcp.port)
        w.write(HANDSHAKE)
        await r.readexactly(7)
        w.close()
        await asyncio.sleep(0.05)
        r, w = await asyncio.open_connection("127.0.0.1", tcp.port)
        w.write(HANDSHAKE)
        await r.readexactly(7)
        await asyncio.sleep(0.3)
        self.assertNotIn(False, bridge.avail)

        # Stay away past the grace period: goes offline.
        w.close()
        await asyncio.sleep(0.3)
        self.assertEqual(bridge.avail[-1], False)

if __name__ == "__main__":
    unittest.main()
