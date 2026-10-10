# Huum local control — setup

Goal: run the Huum sauna controller locally via Home Assistant, no vendor cloud.

## Facts established from packet captures

- Controller: `192.168.1.199`, MAC `80:f3:da:4c:ea:0c`, model `UQ4.2 EU WiFi`,
  firmware `UQ4-4.4.18.0-5c1a4c75-16` (shares build hash `5c1a4c75` with the
  confirmed-working build in kpalang/huum-controller#4).
- HA OS host: `192.168.1.164` (Raspberry Pi, aarch64).
- UniFi gateway / LAN DNS: `192.168.1.1`.
- Default behaviour: controller connects to AWS IoT MQTT over **TLS on 8883**
  (`a1i4dij3rgw057-ats.iot.eu-central-1.amazonaws.com`).
- **Only when 8883 is unreachable** does it fall back to the legacy **plaintext
  channel: `api.huum.eu` TCP 6969**. Confirmed working (0x0B greeting etc.).

## The three conditions that must hold

1. Block TCP 8883 outbound from the controller — PERMANENT (forces plaintext fallback).
2. `api.huum.eu` resolves to the HA host `192.168.1.164` (local DNS override).
3. huum-controller add-on listening on `192.168.1.164:6969`.

## Steps

### 1. Firewall block (UniFi) — keep the rule you already made
- Source `192.168.1.199`, Dest Any, Proto TCP, Dest port 8883, Action Drop.
- Verify it stays above any allow rules.

### 2. Local DNS record (UniFi)
- Settings -> Routing (or Policy Engine, version-dependent) -> DNS Record -> Create.
- Type: A, Name: `api.huum.eu`, IP: `192.168.1.164`.
- (Controller uses the gateway `192.168.1.1` for DNS, confirmed in captures, so
  this override will reach it.)

### 3. Install the add-on (Home Assistant)
- Settings -> Add-ons -> Add-on Store -> top-right menu -> Repositories.
- Add: `https://github.com/weidi/ha-addons`
- Install **Huum Controller**.
- Configuration:
  - `update_frequency: 60`
  - `debug_protocol: 1`   (verbose; turn to 0 later)
- Start the add-on. Enable "Start on boot" and "Watchdog".

### 4. Bring the controller over
- Power-cycle the controller (unplug/replug) so it re-resolves api.huum.eu.
- Watch the add-on Log tab for the 0x0B greeting from `80:f3:da:4c:ea:0c`.

### 5. Test the HTTP API (port 8080)
```bash
curl http://192.168.1.164:8080/status
curl -X POST http://192.168.1.164:8080/start \
  -H "Content-Type: application/json" \
  -d '{"targetTemperature":75,"durationHours":2}'
curl -X POST http://192.168.1.164:8080/stop \
  -H "Content-Type: application/json" \
  -d '{"targetTemperature":75}'
```

## Notes / open items

- The add-on is HTTP-API only (no climate/switch entities yet). Upstream's
  native HA forwarder is broken and patched out at build time. Building proper
  HA entities (climate + light) on top of the /status, /start, /stop API is the
  next project.
- Protocol message type `0x0E` seen in captures is not in the original docs;
  check the add-on parses/ignores it cleanly (watch log with debug_protocol:1).
- To temporarily restore cloud/app control: remove the 8883 block.
