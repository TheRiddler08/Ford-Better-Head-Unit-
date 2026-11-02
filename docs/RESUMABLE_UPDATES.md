# Automatic updates, source-copy editing and interrupted radio transfers

Duet automatically prepares updates while Internet is available, including Internet
shared by Pi over the switch. Its signed-ready-bundle reconciler now also advances
the ordinary Duet checkout, runs tests, updates installed role services, and queues
automatic kiosk activation. Local modifications/ignored-path collisions are
preserved rather than overwritten. A failed candidate test restores the prior
checkout. This closes the previous gap where downloaded helpers could wait until
a future display login to activate.

## Normal installation

The existing role installers install/start the resumable client and radio-relay
services by default; no extra enable command is needed:

```sh
cd ~/car-dashboard
git pull --ff-only
bash install.sh duet  # or tiny / pi, on that device
```

On the home Linux server, `bash install.sh home` installs the existing browser
control panel and ChirpStack bridge as system services. It preserves an existing
`~/.config/car-home/control.env`, creates missing signing/link credentials locally,
and installs native cryptography/MQTT/serial dependencies. It does not publish
secrets to Git or print them. Use the existing authenticated home SSH/Tailscale
connection; the home panel remains loopback-only. Its panel and device credentials
are separate. Configuring the initial home hostname/tunnel and distributing the
device token remain normal machine authentication; software cannot guess them.

Device clients reuse `~/.config/car-dashboard/device-token` and the normal home
SSH tunnel at `http://127.0.0.1:8791`. Alternate HTTPS/tunnel settings belong in
root-owned `/etc/car-dashboard/update-link.json`; HTTP credentials are allowed
only on the localhost tunnel, HTTPS retains certificate verification, and redirects
never forward credentials. On their first authenticated connection clients pin
the home's Ed25519 public key and application link key. Changed keys fail closed
until trusted rotation; reconnects never silently replace an existing key.

## Edit the actual reported copy

In the home panel, open **Software copy editor**, choose Tiny or Duet and a file,
then **Load current device copy**. Devices report their actual active source and
its SHA256 over the authenticated IP link. The editor does not assume that home
Git HEAD is what the car is running. It shows the report time and source hash.

Edit the copy and select **Sign & queue changes**. The home server checks the
reported base, builds character-level edits, signs the patch plus its delivery
descriptor, and stores the job. Unchanged copies generate no transfer. A second
change for the same file waits for the first job to finish, and a stale base is a
conflict requiring a fresh copy—not an instruction to overwrite local work.
Python/shell candidates are syntax-checked before queueing; clients check code
again before activation (JavaScript uses Node syntax checking when available).
Do not treat syntax checks as proof of correct visual or vehicle behavior.

This small-code channel supports the approved car web assets, OBD/GPS recorder
sources and Duet display helpers. It uses a separate signed overlay, preserving
the Git checkout. Privileged fleet/updater code, firmware, arbitrary file paths,
Home Assistant container images and OS packages remain on the independently
verified Git/runtime path. This is not a generic root command channel. Signed source nevertheless runs as
the normal host account, not in the custom-layout JavaScript sandbox. That account
on Duet holds the privileged courier key, so the home software-signing/publishing
authority must be protected as full deployment authority. One job
updates one file; publishing interdependent files separately does not create an
atomic multi-file release. Use the Git/runtime path for those releases.

## Wi-Fi ↔ LoRaWAN resumption

Both paths carry the **same signed descriptor, update ID and authenticated binary
fragments**, not unrelated Wi-Fi/radio packages. Losing Wi-Fi does not reset the
job. Radio can carry the remaining fragments; Wi-Fi can later finish it. Out-of-
order and duplicate fragments are safe, and sender/receiver SQLite journals use
WAL plus FULL synchronization. Process restarts retain partial data, sent attempts
and confirmed receipts. No incomplete payload is installed or executed.

The protocol distinguishes:

- queued/sent: transport attempts, not delivery;
- receiving: persisted fragments, with a receipt only for the intended role;
- ready: the full payload matches its signed size/hash;
- staged: code validated, waiting for adopted-service/display activation;
- applied: device confirmed installation/activation;
- failed: checksum/base/signature/activation failure; no success acknowledgment.

The entire signed manifest must be received and validated for the correct role
before a manifest receipt is sent. This prevents another node on the same switch
from acknowledging a transfer intended for an offline device. Payload receipts
identify exact fragments. Lost receipts cause retransmission, not skipped chunks.
A repeated final fragment solicits an applied receipt if the original ACK was
lost. Failed candidates require a corrected new job rather than endless unsafe
reinstallation. Source hashes, chunk counts, phases and last transport appear in
the panel. Transmission attempts and receipt events are persisted separately.

The compressed descriptor adds overhead. Normal frames are 42 bytes with a
20-byte content fragment; the parser caps application frames at 46 bytes.
`CAR_UPDATE_CHUNK_BYTES` on home can reduce fragments to 4 bytes (26-byte frames).
Not every regional data rate supports even that minimum. The modem/network-server
profile must permit the configured frame size; this is not overridden by the app.
A small text edit can still require many downlinks and take hours or days at
conservative quotas. Large Git bundles/container layers do not silently fall
back to radio. Only jobs in this shared small-delta format can switch transports;
an unfinished large courier ZIP is not converted to radio frames.

## Radio support: explicit hardware boundary

The existing ChirpStack v4 MQTT bridge now feeds the durable shared queue and
processes device receipts. It supports Class A uplink-triggered downlinks or
explicitly configured Class C pacing. QoS/broker acceptance is never called
installation success. Daily downlink limits and send spacing persist across
restarts. The LoRaWAN network server still enforces region, dwell/airtime, receive
window, session-key and frame-counter rules.

The radio may be attached to Pi or Tiny. All roles have an installed relay service;
only the host with a configured adapter runs it. Frames/ACKs are relayed over the
switch on UDP 8796 (the normal role installer allows only its car-subnet Ethernet path), authenticated with the application key, so a Duet need not own
the physical radio. The Tiny also accepts the legacy authenticated settings
protocol, with persisted reception, ordered retries and repeated-ACK handling.
Only Tiny applies those old unaddressed settings; signed code uses the new
recipient-specific protocol. Legacy executable patch commands are not promoted
into the new privileged updater.

**A real modem driver/network join is not yet configured for your hardware.**
No model, serial device, gateway, region, DevEUI/OTAA credentials or ChirpStack
application has been supplied. The installer reports this as `unconfigured` and
does not guess a serial port (which could be GPS/OBD) or issue vendor commands.
The stable adapter boundary accepts a trusted local command via
`update-link.json`:

```json
{
  "radio_adapter": ["/absolute/path/to/your-modem-adapter"],
  "max_app_bytes": 46,
  "uplink_spacing": 60,
  "uplinks_per_day": 120
}
```

The utility receives JSON lines `{ "id": "...", "uplink": "hex" }` or
`{ "poll": true }`; it returns `{ "sent": "..." }` after actual modem transmission
and `{ "downlink": "hex" }` for a received application payload. It must implement
the actual vendor join/session/storage commands and compliant radio scheduling.
The relay's uplink outbox is durable, retries after adapter failure and reserves
its own conservative budget; a modem TX receipt is still not a home applied ACK.
The command is local/root-configured, never supplied by a network packet. A modem
model is needed to implement/verify its native utility; this boundary alone is
not a claim of a fully tested end-to-end radio link.

Set the actual ChirpStack host/application/DevEUI/MQTT credentials in the preserved
home environment file. TLS is supported and does not disable certificate checks.
If these values are absent, the installed bridge waits rather than inventing a
connection. The installed home supervisor watches its private environment file and reloads
services when credentials/settings change; device JSON settings are watched too. No actual radio transmission has been
validated in the cloud tests.

## Automatic activation and reboot handling

Duet helper changes create a managed backup and activation request. A graphical
supervisor restarts its private kiosk process group automatically, checks the new
proxy for 30 seconds and restores the complete previous helper set on startup
failure. The root reconciler can migrate an older running supervisor using only
that user's known graphical-session variables, without dumping its environment.
An absent graphical session waits for normal desktop autostart, rather than
launching a browser against an invented display. Courier/download processes are
separate and are not killed by the kiosk restart.

Tiny's original web pages reload after a committed Git/overlay version change,
waiting while a text/select editor is focused. OBD/GPS overlays run only as the
ordinary recorder user. The fixed root activation handler restarts only adopted,
currently running recorder services and restores the prior overlay on startup
failure. A deliberately stopped recorder stays stopped. The basic startup checks
cannot establish that every measurement/visual is semantically correct; inspect
the actual device after substantive code changes.

An OS `/var/run/reboot-required` flag queues automatic reboot handling. It does
not reboot merely because a download or fragment arrived. Reboot waits for update
locks and a **fresh root-owned verified Park input** in
`/run/car-dashboard/power-state.json` (`parked: true`, `checkedAt` within 10 seconds).
The current installation has no verified Park signal, so a necessary reboot is
reported as `pending-verified-park`. Zero speed, stale GPS, engine stop/start or
missing OBD replies are not substituted for Park. `reboot_updates: false` is an
explicit role override. Do not fake the signal to bypass this while driving.
Retained power is still needed for clean ignition-off shutdown; see
[power-loss protection](POWER_LOSS.md).

## Status

On a device, `~/.local/state/drive-console/software/status.json` reports phases;
`radio-status.json` reports the actual adapter condition. Systemd logs are
`car-update-link`, `car-radio-adapter` and `car-fleet-refresh`. OpenRC installs the
same services and logs to `/var/log/`. Home uses `car-home-control` and
`car-home-radio`. Tiny's update API includes software/overlay/reboot status.

Automated validation covers real authenticated HTTP source-copy delivery,
transport-neutral interruption/restart recovery, out-of-order/missing/duplicate
frames, tampering, role isolation, durable budgets, key pinning, helper rollback,
process-group isolation and the Park-gated reboot decision. Native package and
service syntax checks complement those tests. Physical LoRaWAN, GUI rollback,
firmware power-on and actual vehicle Park input still need device validation.
