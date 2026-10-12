# Changelog

## 0.1.1

- The controller drops and re-opens its connection roughly every 40 minutes.
  Entities no longer flap to unavailable: the device is only marked offline
  after `offline_grace_seconds` (default 120) without a reconnect.
- Fix a replaced controller connection marking the device offline even though
  the new connection was already live.

## 0.1.0

- Initial release.
- Terminates the Huum controller plaintext protocol on TCP 6969.
- Exposes a climate entity via MQTT Discovery with current/target temperature
  and heating action derived from the controller's 0x08 state reports.
- Session duration (number) and session end-time (sensor) entities.
- Protocol encode/decode and framing covered by unit tests built from real
  packet captures.
