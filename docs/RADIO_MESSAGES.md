# Encrypted messages and read-only health requests

This feature is installed through the normal Tiny/Duet/Pi/laptop role updates.
The Tiny owns the commissioned RAK3172. Each enrolled role has a local durable
message worker (`car-messages.service`) and shares the Tiny radio over the car
Ethernet relay. The existing authenticated frames and libsodium public/private
key boundary protect every application payload; no plaintext fallback exists.

## Use it

Update car devices through the **Duet courier**, as usual. On the home server,
update its `~/car-dashboard` checkout and run `bash install.sh home` to apply
the home services. Existing pairing and keys are retained.

- Home panel: **Encrypted radio messages** has a destination selector, text box,
  health-request button and persistent inbox/outbox. Its usual panel token is
  required. It refreshes delivery state automatically.
- Duet's original console: **Updates → Open encrypted radio messages and health
  requests**, or `http://127.0.0.1:8877/radio-messages`. This uses the existing
  authenticated display tunnel to the Tiny, so its sender identity is Tiny.
  Existing native HA dashboards and edited layouts are not reseeded or changed.
- On any enrolled car machine, its local CLI keeps that role's sender identity:

```bash
cd ~/car-dashboard
python3 -m control.messages send 'Arriving soon' --to home
python3 -m control.messages status --to home
python3 -m control.messages list
```

Text is limited to **240 UTF-8 bytes**; up to 100 outgoing messages can wait for
delivery. Empty text, unknown destinations, executable actions and invalid fields
are rejected. Health requests return only role, uptime, local free space and
pending-message count. There is no shell execution, GPS/OBD collection, file
access API, vehicle control, or arbitrary remote-command feature here.

The home and car pages now share a responsive interface with sent/received and
waiting/cancelled filters, search, queue counts, a live UTF-8 byte counter, and
readable health replies. Browser drafts survive reload and connection failures;
credentials are never put in draft storage. Send retries use a client identity
so a lost HTTP response does not create a second copy of the same message.

Queued messages show waiting, transport progress or awaiting-receipt detail.
These counters describe application-fragment transport attempts, not confirmed
radio reception or a delivery-time estimate. Cancellation stops local retries
and sends authenticated retirement notices to the relay; it cannot recall data
already transmitted or queued at the gateway. A late receipt never changes a
cancelled item to delivered. Delivered items cannot be recalled. CLI cancellation:

```bash
python3 -m control.messages cancel 'MESSAGE_ID_FROM_LIST'
```

Car-origin radio messages target home; home can select Tiny, Duet, Pi or laptop.
Direct car-to-car chat is not configured by this feature.

## Delivery behavior

Messages remain `queued` after modem/broker transmission. They become `delivered`
only after an authenticated receipt from a receiving application that saved the
message. Delivered does not mean read. Recipients have persistent, deduplicated
inboxes; interruption and restart retain fragments and pending acknowledgments.
Messages for another role are neither stored in its inbox nor acknowledged.
Authenticated completion notices retire the Tiny relay's old retry frames.

The car and home have the same IDs, text, source/destination labels and receipt
semantics. Role labels describe enrolled software sources; the existing shared
application key is not separate cryptographic authorization per LAN computer.
Local message storage remains readable, like existing local car records.

Class A downlinks need uplinks. After the diagnostic finishes, the configured
RAK3172 relay sends a sealed, non-telemetry keepalive when otherwise idle, at a
default spacing of 900 seconds. `idle_uplink_spacing` controls this interval;
`radio_keepalive:false` disables it. Existing persisted uplink/downlink budgets
still apply. A very small data-rate payload or exhausted daily budget can make a
message take hours or longer; this is not chat-speed Internet. Status-only serial
queries no longer consume the transmit budget. User messages/receipts take
priority over the diagnostic; queued updates retain their existing scheduling.

**GPS radio reporting remains deferred.** `gps_radio_enabled` defaults to false.
The catalog worker does not read or queue GPS samples in that mode, and the Tiny
relay retires older queued GPS catalog frames locally before opening the adapter.
Local GPS recording, navigation, routes and history remain independent. No
diagnostic result is changed to passed to unlock messaging.

Camera catalog metadata and existing signed small-update services remain
available through their separate queues. Full video, maps and bulk files stay
local or use ordinary IP. Mobile gateway/mesh and future camera AI are still the
separate planned features documented in the project tracker.

## Verification

Automated tests cover bidirectional delivery, restart, lost/duplicate fragments,
wrong recipients, shared encryption with small US915 payloads, idempotent health
replies, queue/content validation, GPS opt-out, and browser queue persistence and
safe text rendering. These are software checks, not a claimed physical radio
delivery result on this installation.
