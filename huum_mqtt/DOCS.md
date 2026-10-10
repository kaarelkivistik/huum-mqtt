# Huum MQTT

Local control of a Huum sauna controller through Home Assistant, with no vendor
cloud. The add-on terminates the controller's legacy plaintext protocol on TCP
`6969` and exposes it to Home Assistant as a **climate** entity via MQTT
Discovery.

## How it works

Modern Huum firmware talks to AWS IoT over MQTT/TLS (port 8883), but still falls
back to a legacy **plaintext** channel to `api.huum.eu:6969` when 8883 is
unreachable. This add-on impersonates that endpoint on your LAN.

```
controller --(TCP 6969, plaintext)--> add-on --(MQTT)--> Mosquitto --> Home Assistant
```

## Prerequisites (network)

These are done on your router/DNS, once:

1. **Block the cloud channel.** Drop outbound TCP `8883` from the controller's
   IP (by port, not by IP address — the AWS endpoint rotates). This forces the
   plaintext fallback.
2. **Redirect the endpoint.** Make `api.huum.eu` resolve to your Home Assistant
   host's IP on your LAN (local DNS record).
3. An MQTT broker (the **Mosquitto** add-on) configured in Home Assistant.

Without steps 1 and 2 the controller stays on the cloud and never connects here.

> Disabling the controller's "remote heating safety" feature may be required for
> remote start to actually energise the heater. Understand the safety
> implications before doing so.

## Configuration

| Option | Default | Meaning |
| --- | --- | --- |
| `update_frequency` | 60 | How often (s, 0-255) the controller reports status |
| `min_temp` | 40 | Lower bound of the climate setpoint |
| `max_temp` | 110 | Upper bound of the climate setpoint |
| `default_target` | 70 | Setpoint used when turning on before one is chosen |
| `default_duration_hours` | 3 | Heating session length sent on start |
| `max_duration_hours` | 12 | Upper bound of the duration number entity |
| `debug` | false | Verbose protocol logging |

## Entities

Created automatically via MQTT Discovery, grouped under one device (keyed on the
controller's MAC):

- **Climate** – off/heat, current temperature, target temperature, and action
  (heating/idle/off). State is derived from the controller's own reports, so
  starts made from the physical panel show up here too.
- **Session duration** (number) – hours sent to the heater when it is turned on.
- **Session ends at** (sensor) – when the current heating session will stop.

## Notes & limitations

- The heater self-regulates to the setpoint; this add-on passes the target
  through rather than doing on/off control itself.
- HUUM requires a session duration; HA's climate model has none, hence the
  separate duration entity.
- The `0x0E` protocol telemetry is currently ignored.
