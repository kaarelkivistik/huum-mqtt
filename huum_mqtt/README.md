# Huum MQTT add-on

Local Home Assistant control of a Huum sauna controller via MQTT Discovery — no
vendor cloud. Exposes a `climate` entity (plus session duration and end-time
entities) by terminating the controller's legacy plaintext protocol on TCP 6969.

See [DOCS.md](DOCS.md) for setup (network prerequisites, options, entities).

## Status

Reverse-engineered and tested against firmware `UQ4-4.4.18.0-5c1a4c75-16`
(`UQ4.2 EU WiFi`). Protocol encode/decode is covered by unit tests built from
real packet captures (`test/`).

## Development

```bash
python3 -m unittest discover -s test -v
```

Layout:

- `huum/protocol.py` – wire encode/decode + framing (pure, unit-tested)
- `huum/tcp_server.py` – asyncio server for the controller link (port 6969)
- `huum/state.py` – in-memory device state model
- `huum/mqtt_bridge.py` – MQTT Discovery + command handling
- `huum/main.py` – wiring + reconnection
