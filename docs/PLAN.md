# Huum MQTT add-on — plan

A from-scratch Home Assistant add-on that bridges the Huum sauna controller's
local TCP protocol to Home Assistant via MQTT Discovery, with real heater
on/off state derived from the device's status reports.

## Why from scratch (not fork kpalang/weidi)

- kpalang's `forwarder.ts` (the HA bridge) does not exist in the repo; the HTTP
  API only exposes current temperature. No heater state, no entities.
- weidi's add-on wraps kpalang over HTTP + ingress; extending it means forking a
  Bun/TS add-on that re-fetches upstream at build time (fragile).
- We own the protocol knowledge now. A dedicated Python add-on with MQTT
  Discovery is cleaner and maintainable.

## Environment (established)

- Controller `192.168.1.199`, MAC `80:f3:da:4c:ea:0c`, model `UQ4.2 EU WiFi`,
  firmware `UQ4-4.4.18.0-5c1a4c75-16`.
- HA OS host `192.168.1.164` (Raspberry Pi, aarch64). Mosquitto add-on present.
- UniFi gateway/DNS `192.168.1.1`.
- Local path requires: (1) block TCP 8883 from controller, (2) `api.huum.eu` ->
  `192.168.1.164`, (3) add-on listens on 6969. All confirmed working.
- Remote-heating safety feature was disabled on the controller so remote start
  energises the element.

## Protocol spec (from kpalang src + our packet captures)

Timestamps are little-endian uint32 unix seconds.

### Incoming (heater -> add-on)
- `0x0B` HANDSHAKE (77 B): MAC `[1:7]` reversed; firmware ascii `[32:57]`;
  name ascii `[62:73]`.
- `0x09` SENSOR_READING: `[1]`=temperature C, `[3]`=ping frequency s,
  `[4]`=STATUS code, trailing bytes carry extra telemetry (incl. an embedded
  0x0E-like block in the long form).
  - Captured idle: `09 17 00 3C 26 02 00 00 00 00 00` -> temp 23, freq 60,
    status 0x26.
- `0x08` CLOUD_UPDATE: logged by upstream; semantics TBD.
- `0x0E` (undocumented): `0E <int32 = -24> <int32 = 125> <timestamp>`, periodic
  (~40s). Purpose unknown. Log/ignore for v1.

### Outgoing (add-on -> heater)
- `0x02` frequency: `02 <ts> <delay:1B> 00` (7 B). delay 0..255 s.
- `0x07` ON (24 B): `07 <temp> 00 00 00 00 03 <startTS> <endTS> <startTS>
  EC C5 EF 10 00`. endTS = startTS + durationHours.
- `0x07` OFF (24 B): `07 <temp> 00 00 00 00 03 00 00 00 00 00 00 00 00
  <ts@offset15> 75 59 FC 10 00`.
- The `03` at offset 6 and the trailing constants are kpalang's hardcoded
  "unknown" values; accepted by our device. Keep.

### Heater state — RESOLVED (from heating-session log 2026-10-09)
Heating state is NOT in the 0x09 status byte. It comes from **0x08**.

- `0x08` CLOUD_UPDATE / state report (25 B):
  `08 <temp> 00 00 00 00 03 <startTS 4B> <endTS 4B> <reportedTS 4B> 00000000 01 00`
  - heating start -> startTS/endTS populated (start, start+durationHours).
  - heating stop  -> startTS/endTS zeroed.
  - **heating = startTS != 0.** endTS gives session end (nice attribute).
  - Sent event-driven on state change, INCLUDING app/panel-initiated starts
    (first 0x08 in the log had no preceding 0x07 from us). This is our
    external-start detection.
- `0x09` byte `[4]` status stays `0x26` (online) whether idle or heating -> not
  a heating indicator. byte `[5]` was `0x02` idle vs `0x00` heating (single
  sample) -> candidate continuous indicator / post-restart recovery; VALIDATE
  later (also disambiguate from door).
- Upstream enum (0x23/0x24/0x25) does not apply to this firmware.

### State model (settled)
- current_temperature <- 0x09 [1]
- target_temperature  <- 0x08/0x07 [1]
- hvac mode/action    <- 0x08 startTS (non-zero = heat/heating; zero = off/idle)
- attribute session_ends_at <- 0x08 endTS

### Still open (non-blocking)
- byte `[5]` of 0x09 as continuous heating/recovery signal (needs a sample set).
- door-open bit location (optional binary_sensor).
- 0x0E and the trailing 0x09 telemetry semantics (defer).

## Architecture

Python 3 + asyncio + aiomqtt, Docker add-on.

```
huum-mqtt/
  config.yaml          # manifest: ports 6969/tcp; services: ["mqtt:need"];
                       # options: update_frequency, default_duration_hours,
                       #          min_temp, max_temp, debug
  build.yaml           # base images per arch (aarch64, amd64)
  Dockerfile
  run.sh               # s6 / bashio entrypoint; reads options + mqtt service
  requirements.txt     # aiomqtt (paho)
  huum/
    __init__.py
    protocol.py        # encode/decode; pure functions; unit-tested on real hex
    tcp_server.py      # asyncio server; holds heater socket; handshake->0x02
    mqtt_bridge.py     # discovery + state publish; command subscribe -> TCP
    state.py           # device state model (temp, target, mode, action, avail)
    main.py            # wire TCP <-> state <-> MQTT
  test/
    test_protocol.py   # assert encode/decode against captured payloads
  DOCS.md README.md CHANGELOG.md
```

### MQTT
- `services: ["mqtt:need"]` -> Supervisor injects broker host/port/user/pass
  (read via bashio `services mqtt`). No manual broker config.
- Discovery device keyed on heater MAC -> stable unique_id, survives restarts.
- Entities:
  - `climate` huum_sauna: modes [off, heat]; target temp cmd+state; current
    temp; `action` (heating/idle/off) from the 0x09 status byte; min/max/step.
  - availability: MQTT LWT + heater-socket-connected flag.
  - optional `binary_sensor` door; optional `number` session duration.
- Retained state topics so HA restores target/mode across restarts.

### Control semantics
- mode heat -> `0x07` ON(target, duration). duration from number entity/option.
- mode off  -> `0x07` OFF(target).
- set temperature -> store; if currently heating, re-send ON(new target).
- action/mode corrected from device 0x09 (detects panel-initiated starts).

## Milestones
1. Capture heating session; finalise status-code map. (BLOCKS accurate state.)
2. protocol.py + tests green against real payloads.
3. tcp_server.py: accept heater, handshake, 0x02, stream readings.
4. mqtt_bridge.py: discovery + state + command round-trip.
5. Package add-on; install on HA; end-to-end test; iterate.

## Open questions / risks
- Heating status code (milestone 1).
- durationHours max / HUUM session cap -> pick a safe default (3h) and max.
- Temperature range for UI (propose 40-110 C, configurable).
- 0x0E and 0x08 semantics (defer).
- Language: Python (recommended) vs reuse kpalang TS.
