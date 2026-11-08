# LoRaWAN implementation and test tracker

## Confirmed physical baseline

User verified an OTAA join (`+EVT:JOINED`, `AT+NJS=1`) and an uplink
containing `Hello` (`SGVsbG8=`) in ChirpStack on 2026-11-07.

- Home: RAK7266S, gateway EUI `ac1f09fffe217e15`, Semtech UDP to the
  home Ubuntu server's LAN address on port 1700. Gateway radio channels 8–15
  plus 65, public LoRaWAN network.
- ChirpStack enabled region **and device profile**: `us915_1`.
- Car Tiny, not Pi: USB RAK3172-E, RUI 4.2.4, DevEUI
  `ac1f09fffe29d8b2`, US915 (`BAND=5`), mask `0002`, OTAA, Class A.
- The USB CH340 identifier alone does not identify its attached hardware.
  Do not point the OBD recorder at the LoRa modem.

Do not reset keys, change masks or restart join loops as a routine test.
Keep private keys and AppKey out of logs and Git. The initial `Hello` was a
manual commissioning sample, not the production encrypted application protocol.

## Explicitly deferred TODO

- **Automatic GPS reporting and its hardware test are deferred at the user's
  request.** Do not enable GPS transmission as part of these tests. When resumed,
  use the normal local GPS recorder and existing libsodium public/private-key
  radio boundary. Keep local GPS history readable and unencrypted.

## Current feature gates

| Feature | Software validation | Physical validation / remaining work |
| --- | --- | --- |
| OTAA and small uplink | Existing LoRaWAN firmware | Confirmed by user |
| RAK3172 serial adapter | PTY tests; explicit device, read-only probe, TX event handling, asynchronous RX | Run probe on actual RUI 4.2.4; verify event format and downlink |
| Class A downlink | RX parser and server bridge tests | Queue a downlink, then trigger uplink; RX follows uplink only |
| Public-key encrypted application frames | PyNaCl sealed boxes, tamper/wrong-key rejection, fragmentation, restart tests | Enroll pinned keys over authenticated IP; encrypted round trip still pending |
| Automatic encrypted radio self-test | Durable random challenge/echo; serial + MQTT encrypted integration test, wrong-nonce/replay/expiry tests | Pending on physical devices; join/Hello/Pong alone do not establish application enrollment |
| Shared Tiny/Duet/laptop queue | Existing Ethernet relay and durable transfer tests | Submit from two car LAN devices and verify scheduling on the Tiny |
| Small messages/status | Existing authenticated frames | Application message UI/inbox and encrypted ping/pong need integration |
| Small settings/file deltas | Existing signed, resumable transfer tests | Loss, duplicate, restart and IP/radio handoff on real hardware |
| GPS reporting/coverage | Existing local recording and encrypted event/coverage code | Deferred; do not infer coverage from gateway online status |
| Camera clip catalog | Metadata-only event queue | G980H ingestion and metadata round trip need physical validation |
| Video, maps, bulk files | Local storage and normal IP only | Never schedule over LoRaWAN |
| Vehicle gateway/mesh | Planning only | Separate concentrator/backhaul hardware not installed; USB node is not a gateway |

## Read-only Tiny radio check

Close picocom (Ctrl+A, Ctrl+X) and stop any other process using the serial device.
From the updated checkout on the Tiny:

```bash
cd ~/car-dashboard
sudo python3 -m control.rak3172 \
  --device /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 --probe
```

This queries firmware, mode, band, mask, OTAA mode, class, DevEUI and joined
status. It does **not** query AppKey, transmit, reset, join or reconfigure anything.
The device must identify itself as a RAK3172 before further queries proceed.
Normal serial adapter operation runs as the installed user's `dialout` group.

## Integration with automatic role services

The normal role installation/update already installs `car-radio-adapter.service`.
Its existing `/etc/car-dashboard/update-link.json` settings now also accept:

```json
{
  "radio_model": "RAK3172",
  "radio_device": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
  "radio_fport": 10,
  "max_app_bytes": 11,
  "uplink_spacing": 60,
  "uplinks_per_day": 120
}
```

**Merge into existing settings; do not replace enrollment credentials or IP
configuration.** An explicitly configured `radio_adapter` command still takes
precedence. No automatic port selection or key changes occur. Missing enrollment
keeps the service unconfigured; the manual radio join does not enroll application
encryption keys. Do not enable production queues until enrollment and downlink
tests are complete. Automatic RAK3172 payload sizing now queries the actual
US915 data rate: 11 bytes at DR0, up to 46 at supported higher rates. Fixed mode
is available with `radio_auto_payload:false`. See [home HA integration](HOME_HA_LORAWAN.md)
for the separate US915 downlink setting. Encryption overhead and airtime budgets
still apply.

The adapter implements the existing JSON-lines boundary:
`{"id":"attempt-id","uplink":"sealed-fragment-hex"}` emits
`{"sent":"attempt-id"}` only after a modem TX-completion event, never `OK`
alone. RX events on the configured application port emit `{"downlink":"hex"}`;
the existing gateway verifies/decrypts them. TX completion is not a home receipt
or proof of installation. An ambiguous timeout keeps the application queued.
The adapter exits on device loss; the normal role service retries.

`{"poll":true}` only queries join status. It does not send a plaintext dummy
heartbeat or open a Class A receive window. Scheduled encrypted keepalives for
idle downlink reception remain TODO; until then downlinks require a queued uplink.
Class C switching and automatic rejoins are intentionally not implemented here.

## Automatic encrypted round-trip check

If an older `systemctl --user` deployment still runs `car-home-panel.service`,
it may own port 8790 with different tokens while the new system service retries
`Address already in use`. The normal home installer now retires that exact
legacy user unit, keeps its state and credential files, and verifies the new
listener accepts the configured device token before reporting success. A first
install reuses legacy token files and an existing software link key; an existing
`control.env` is preserved. Do not reset encryption keys to repair a port conflict.

### One-time pairing for the commissioned Tiny

After installing the home services, configuring their ChirpStack application and
MQTT connection, and updating the Tiny through its normal verified update path,
close picocom and run as the Tiny's ordinary dashboard user:

```bash
cd ~/car-dashboard
python3 -m control.pair_radio --home cayden@100.121.219.15 \
  --device /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
```

This deployment-specific pairing entry point checks the previously commissioned
DevEUI `ac1f09fffe29d8b2` and joined US915 OTAA Class A status using read-only
queries. It temporarily releases/restores the radio service to probe as the
ordinary user with dialout access. It does not change radio settings. Initial
Tiny sudo authorization and the home user's SSH password/host verification may
each be needed once. The home device token is transferred inside authenticated
SSH, captured in memory, stored mode 0600 and never printed. An existing different
Tiny device token or changed application public-key pin is not overwritten.

A dedicated Ed25519 forwarding key is authorized on home with `restrict`, one
`permitopen="127.0.0.1:8790"` destination, and a forced `/bin/false` command;
it cannot run commands or copy files. Existing home authorized keys are preserved.
The normal verified Tiny role installer manages `car-home-tunnel.service`, with
loopback-only local port 8791, strict host checking, unattended authentication,
keepalives and automatic restart. The setup command requests sudo only to copy
validated settings as data and start existing verified reconciliation/services;
it does not execute editable Python as root. Explicit opt-outs remain preserved.

The existing role configuration fingerprint includes managed tunnel settings, so
later configuration changes and normal automatic updates reconcile this unit.
The port forward is unrelated to the laptop browser's ChirpStack UI tunnel.
Pairing closes with an authenticated application enrollment and starts the normal
radio service. Missing MQTT, altered credentials, no route to home, a closed
serial device or exhausted airtime budget still leaves a pending/failed test.

Pairing prints its current stage before waiting. SSH connection establishment
is bounded to ten seconds, prompts for the password at most once, and has a
two-minute overall enrollment deadline (including user authentication). Serial
checks and service control operations are also bounded. Verified role setup can
take several minutes and is labelled accordingly. Ctrl+C reports cancellation;
it does not mean a LoRaWAN transmission or encrypted test completed. Rerunning
pairing retains the same forwarding key and checks existing credentials/pins.

Once a Tiny has explicit RAK3172 configuration, the normal radio service has its
existing link key and pinned home public key, and the home bridge has the Tiny's
public key, the service starts **one persisted self-test** automatically. No
manual ping injection is needed. Set `radio_self_test` to `false` in the existing
link settings to opt out. It never enables GPS reporting, scans ports, changes
OTAA keys or sends plaintext. Application enrollment now reports the Tiny's
public key; older Pi radio deployments remain accepted, but a different existing
pin still requires an explicit trusted migration rather than silent rotation.

The test has a random challenge, shared-key application authentication and the
existing libsodium encryption in both directions. Only a matching authenticated
radio echo at the Tiny changes the result to `passed`. An IP result report,
MQTT publication or modem TX event cannot pass the Tiny's test. Encrypted
challenges repeat under the existing persisted radio budget to open Class A
receive windows until completion or a three-hour timeout. With conservative
11-byte uplinks and 60-second spacing this can take roughly an hour or longer;
do not raise the rate or payload size just to speed up testing. A depleted
daily budget, missing bridge or key pin, and radio loss are not success.

The result and plain-language completion message are saved at
`~/.local/state/drive-console/software/radio-test-result.json` on the Tiny.
Completion is printed to `car-radio-adapter`'s journal. The original Updates API
includes `radioDiagnostic`; the normal authenticated IP exchange reports it to
the home panel, which refreshes its result message automatically. The home copy
is labelled an authenticated car report. A completed test is not repeated merely
because a process or machine restarts; its result timestamp identifies that run.

After the normal software update, inspect/wait on the Tiny with:

```bash
python3 -m control.radio_diagnostic --wait
```

This watcher does not configure or transmit anything itself. If pairing or
ChirpStack MQTT integration has not been configured, it says `pending`, not
`passed`. This repository/cloud test runner cannot send a later chat message or
claim a physical result without access to the machines; the running services
publish their result in the journal and dashboard.

## Next hardware test: downlink before application features

With the automatic adapter stopped and picocom open, queue an **unconfirmed**
downlink to Car-Tiny in ChirpStack on port 2. Use Base64 `UG9uZw==` (`Pong`)
as a non-sensitive commissioning sample. Trigger `AT+SEND=2:50696E67` (`Ping`).
Record the complete `+EVT:RX_1:...` or `+EVT:RX_2:...` line without keys.
Do not send real vehicle data this way. A queued downlink alone is not delivery.
If it fails, collect the gateway TX acknowledgement and ChirpStack downlink
error, rather than repeating join/channel changes.

After that passes: enroll application keys, test encrypted non-GPS messages in
both directions, then loss/retry/restart/handoff, multi-client scheduling, and
metadata. Keep each physical result distinct from automated test results.

## Local automated checks (no radio or vehicle data)

```bash
python3 -m unittest tests.test_rak3172 tests.test_radio_config \
  tests.test_radio_crypto tests.test_radio_bridge_updates \
  tests.test_update_transfer -v
```

PTY hardware stand-ins live only in tests and exercise the production serial
adapter; the dashboard has no simulation mode. PyNaCl-dependent checks require
the normal development/CI dependencies. Skipped crypto tests do not establish
encryption correctness.
