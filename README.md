# Huum local add-ons

A Home Assistant add-on repository for controlling a **Huum** sauna heater
locally — no vendor cloud — by terminating the controller's legacy plaintext
protocol and exposing it to Home Assistant over MQTT Discovery.

## Add-ons

### [Huum MQTT](./huum_mqtt)

Bridges the Huum controller (TCP 6969) to a Home Assistant `climate` entity via
MQTT Discovery, with heating state derived from the controller's own reports.
See [huum_mqtt/DOCS.md](./huum_mqtt/DOCS.md).

## Install

In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add:

```
https://github.com/kaarelkivistik/huum-mqtt
```

Then install **Huum MQTT**. Network prerequisites (DNS redirect of `api.huum.eu`
and blocking the cloud MQTT port) are in [docs/SETUP.md](./docs/SETUP.md).

## Docs

- [docs/SETUP.md](./docs/SETUP.md) – network setup for local control
- [docs/PLAN.md](./docs/PLAN.md) – reverse-engineered protocol + architecture

## Development

```bash
cd huum_mqtt
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s test -v
```

The protocol encode/decode and the MQTT round-trip are covered by tests built
from real packet captures; the integration test needs `mosquitto` on `PATH`.
