# Changelog

## 0.1.0

- Initial release.
- Terminates the Huum controller plaintext protocol on TCP 6969.
- Exposes a climate entity via MQTT Discovery with current/target temperature
  and heating action derived from the controller's 0x08 state reports.
- Session duration (number) and session end-time (sensor) entities.
- Protocol encode/decode and framing covered by unit tests built from real
  packet captures.
