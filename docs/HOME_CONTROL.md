# Ford Better Head Unit — home server control and LoRaWAN

This code is under development. It provides the GUI, modular config schema, a car-side patch receiver, a home-side pending queue, and an optional ChirpStack MQTT adapter. **No car radio driver or physical connection has been configured or tested.** No live route or CarPlay implementation is supplied.

## Interface

The car boots into an offline-first, responsive infotainment screen at http://127.0.0.1:8787, with:
- A home/navigation panel with honest GPS/map availability
- Real OBD2 readings from the separate recorder (stale data flagged)
- Local file music player (choose audio in browser)
- Diagnostics, settings, and updates
- A fallback classic telemetry dashboard at /classic.html

Optional GPS position uses an installed gpsd daemon on TCP 127.0.0.1:2947. Map files are offline XYZ tiles at /var/lib/car-dashboard/tiles/Z/X/Y.png; route guidance is not yet implemented. Do not download tiles from a service without permission to store them offline.

## Home control server

Run on your **home server**, not on the car:

```bash
cd Ford-Better-Head-Unit-
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install 'cryptography>=41' 'paho-mqtt>=2,<3'
export CAR_PANEL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
export CAR_LINK_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
python3 -m control.home_server
```

Save the generated token and key **securely** for later use; exporting in an interactive shell is an illustration, not a persistent deployment. Use an environment file with restricted permissions for a long-running service. Do not commit credentials or your car history to Git. Default management address: http://127.0.0.1:8790/. Connect remotely only through SSH/Tailscale forwarding; this application has no HTTPS server. The panel authenticates every API call with its token.

The panel changes UI component order/visibility, day/night mode, MPH/°F, navigation menu items, labels, and polling cadence. Saving appends compact config frames to the persistent per-car queue. They remain pending until a real car ACK arrives. Home and Settings are retained so you cannot hide all access.

## LoRaWAN transport — requires setup

The home-side optional adapter is `python3 -m control.chirpstack_bridge`. Supply:
- `CAR_CHIRPSTACK_MQTT_HOST`, `CAR_CHIRPSTACK_APP_ID`, `CAR_CHIRPSTACK_DEV_EUI`, and MQTT authentication/TLS as appropriate
- `CAR_LORAWAN_CLASS` A or C (Class A can only receive after uplinks)
- `CAR_DOWNLINK_SPACING` and `CAR_DOWNLINKS_PER_DAY` appropriate to your region, duty-cycle/network-server quotas and data rate.
- `CAR_LINK_KEY` must match the car agent.

Frames carry up to 20 user-data bytes in a 46-byte application payload including authentication fields; messages are compressed where useful, split into chunks and reassembled with HMAC verification and a last-sequence replay guard. Missing frame support is implemented in the protocol, but car modem uplink integration and a reliable ACK/retry/state machine must be completed and tested before real deployments. The network server owns MAC scheduling and airtime policies: a 46-byte payload is **not legal at every data rate**. Set a smaller frame payload where required.

**Important:** The car agent currently reads hexadecimal application frames on stdin or via `--receive-hex`, and emits uplink frames on stdout. You need a compatible physical LoRaWAN modem and a bridge which preserves downlinks, forwards ACKs and can retry safely; installing these Python files alone will not make the radio work.

## Secure micro-updates

`control/patches.py` supports signed Ed25519 incremental UTF-8 source edits for approved UI/recorder source paths (not the updater, system services or arbitrary commands). The signing private key remains on your home server; the public verification key goes on the car. Hashes of the original and updated contents must match, and writes are atomic to a separate overlay directory.

This is not a full reliable OTA system yet: a production-grade watchdog, install/boot rollback, modem retransmission, authenticated device identity, update dependency ordering, safe recorder restart, and tests on the actual hardware remain necessary. **Do not enable unattended LoRaWAN code deployments in a moving vehicle.** The updater itself remains GitHub/internet-only. Existing GitHub dashboard updates continue through the independently enabled systemd timer.

## What still needs to be completed

1. Connect your specific USB LoRaWAN modem on the ThinkCentre and ChirpStack device/gateway to the bridge.
2. Persist firmware reception state, ACK/retry flows and state reconciliation across outages.
3. Implement authenticated update manifest/rollout, watchdog, automatic rollback, safe parked installation policy, and code overlay cleanup after GitHub updates.
4. Run a radio/airtime test in the actual regional LoRaWAN data rate.
5. Verify touchscreen rendering and GPS/NAV hardware on the Lenovo.
