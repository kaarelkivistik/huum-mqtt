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


if __name__ == "__main__":
    unittest.main()
