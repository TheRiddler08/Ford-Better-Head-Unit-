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
tests are complete. The default 11-byte payload is conservative for US915 DR0;
use the same encrypted frame budget on the home bridge. Increasing it requires
checking the actual data rate. Encryption overhead means one short application
message may require many uplinks; airtime budgets remain active.

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
