# Car LoRaWAN integration with home Home Assistant

This integration targets the **existing home Home Assistant running in Docker
on the BTT Pi**, using its existing MQTT broker. It is separate from the Tiny's
Car Assistant UI. No replacement dashboard runtime or Home Assistant container
is installed on the BTT Pi.

## Data path

Car recorders → local sensor collector → shared Ethernet radio relay → RAK3172
→ fixed gateway → ChirpStack → authenticated application decryption → home
sensor database → home MQTT broker → Home Assistant MQTT discovery.

`car-telemetry.service` is installed by the normal verified role updates. It
collects actual local uptime/free space on each enrolled car role, plus the
Tiny's existing fresh, normalized OBD sensor readings from its local backend.
It never talks to the OBD adapter directly and never issues vehicle commands.
The only radio metric IDs are speed, RPM, coolant/intake temperatures, ECU
voltage, fuel, engine load, throttle, uptime and free space. **GPS coordinates
have no metric ID and GPS radio reporting remains deferred.**

Sensor batches use compact binary values, application authentication, and the
existing libsodium public/private-key encrypted radio boundary. A maximum of
six values fit in one authenticated application batch, which the existing
radio encryption fragments to the configured regional payload limit. A single
bounded pending batch is retained rather than buffering a historical backlog.
Role labels retain the existing shared-key trust model; they are not separate
cryptographic permissions for each computer on the switch.

Telemetry uses its own journal/service and does not install code or write active
recorder databases. It is a latest-state channel, not a guaranteed historical
log. The full car data remains local. Missing values are never replaced with
fabricated readings or zeroes. Home ignores backwards/duplicate observations,
rejects future/very old timestamps, and makes values older than 30 minutes
unavailable. Entity names say **Last reported**. Long radio delays can mean a
sample is already stale when received; this is reported honestly.

## Home service and broker configuration

The normal `bash install.sh home` installs `car-home-ha.service` alongside the
existing home control and ChirpStack bridge services. It starts in a clear
unconfigured state until the home broker is supplied. It does not assume that
the ChirpStack internal MQTT broker is the Home Assistant broker.

Edit the existing private `~/.config/car-home/control.env` on caydenserver1:

```ini
CAR_HA_MQTT_HOST=YOUR_EXISTING_HOME_MQTT_BROKER
CAR_HA_MQTT_PORT=1883
CAR_HA_MQTT_USER=YOUR_MQTT_USER
CAR_HA_MQTT_PASSWORD='YOUR_MQTT_PASSWORD'
CAR_RADIO_MAX_APP_BYTES=46
```

Use the actual broker hostname/IP (not necessarily the BTT Pi address). Enter
credentials locally; do not paste them into chat or Git. Existing service
credentials and pinned application keys remain unchanged. TLS is supported via
`CAR_HA_MQTT_TLS=1`, normally port 8883, and optionally `CAR_HA_MQTT_CA` for the
trusted CA file. TLS verification is never disabled. The environment watcher
reloads settings automatically, or restart `car-home-ha` after editing.

The 46-byte home setting suits this commissioned US915 downlink plan and avoids
retaining the earlier 11-byte commissioning default. The Tiny now queries the
actual US915 data rate before sending. Automatic mode keeps DR0 at 11 bytes and
uses at most 46 at higher supported rates; a rate decrease fails closed and
refragments queued data. It never changes region, mask, keys or configured data
rate. `radio_auto_payload:false` retains a fixed explicit cap. Persistent modem
attempts retain their original budget, so a rate change cannot falsely advance
another encryption stream.

Home Assistant must have its normal **MQTT integration** connected to that same
broker and discovery enabled. The home bridge publishes retained discovery
under `homeassistant/` and states under `car/car_1/` by default. The broker user
needs permission to publish those discovery/state topics and subscribe to
`car/car_1/command/#`. Existing broker ACLs/authentication are not modified.

In Home Assistant, MQTT adds a device for each car role with the supported
last-reported sensor values, pending/failed update counts, delivery state and
last transport. A health-report button queues the existing encrypted read-only
health request. A separate **Car radio update delivery** switch pauses/resumes
home-side radio update scheduling. It does not cancel installed/staged work,
change signature checks, or pause ordinary IP updates.

The additional diagnostics show the latest observation's age, its last accepted
transport, and whether any sensor observation is still fresh. A newly received
but old radio sample remains stale. Fragment progress reaching 100% means all
fragments were received; the delivery state remains `awaiting-application`
until an applied receipt arrives. File-sync errors are counted separately from
conflicts. Repeated health-button presses are limited to one queued request per
role every five minutes, including across bridge restarts, to conserve airtime.

The MQTT bridge has a retained offline will. Stale retained sensor values are
replaced before bridge availability goes online on reconnect. Read-only status
is saved at `~/.local/share/car-home-control/home-ha-status.json`. Missing broker
settings or a disconnected client are not reported as a working integration.

The bridge automatically reconnects with a bounded backoff when the broker is
unavailable. It watches the standard `homeassistant/status` birth topic and
republishes discovery and fresh states after Home Assistant restarts. Include
subscribe access to this topic in an existing restrictive broker ACL. SQLite
and command processing run outside the MQTT network callback so a slow disk
does not block its keepalive. Retained commands are ignored and the incoming
command queue is bounded.

The authenticated home panel's **Home Home Assistant integration** section now
shows report age, transport, fragment progress and file errors for all four car
roles. A bridge heartbeat older than 45 seconds is shown as `status-stale`;
it cannot leave an old `mqtt-connected` status looking current. These reports
describe observations and delivery receipts, not proof that an unplugged car
is reachable right now.

## Updates, syncing and limits

The home panel now has [per-transfer sender controls](TRANSFER_CONTROLS.md):
pause/resume, IP-only policy, priority, receipt-preserving retry, a paginated
inspector and shared radio-budget visibility. Home HA also exposes paused
transfer counts. Successful IP exchanges hold duplicate source/file radio
downlinks for that role for 45 seconds, then permit automatic fallback again.

Existing small approved UI/recorder/Duet source changes continue to use the
same signed manifest, transfer ID, content hashes, durable chunks and receipts
across IP and LoRaWAN. The home bridge now shares downlink scheduling between
source updates, settings, application events and diagnostics. A diagnostic no
longer has exclusive priority over a queued source update. Receiver storage
ACKs remain distinct from the final applied receipt. HA update progress is
based on actual journal state, never MQTT publication success.

Selected non-executable data files use the separate [selected-file sync service](SELECTED_FILE_SYNC.md),
with the same identity/hash/receipt across IP and radio. Its local folder and
explicit allowlist are separate from executable source updates. File sync
pending/conflict/state entities are also published to home HA.

Full Git/runtime/OS updates still use the Duet courier or ordinary IP. Video,
maps, archives and unselected files are not silently converted into radio
patches. Existing source-update allowlists and safe activation rules stay in place.

Radio airtime and small data-rate payloads remain real limits. Default collection
interval is 900 seconds (`telemetry_interval`, minimum 300). An unfinished batch
is preserved rather than replaced every interval. `telemetry_radio_enabled=false`
opts out and prevents queued telemetry from transmitting at the Tiny radio
boundary. Normal persisted transmit/downlink budgets continue to apply; a small
payload and a low daily quota can make fresh frequent sensor updates impossible.
This software does not claim Internet-rate syncing over LoRaWAN.

When the authenticated home IP exchange is healthy, it carries current sensor
batches directly and the radio collector stays quiet. Both paths verify the
same metric allowlist/authentication, and older delayed radio samples cannot
overwrite newer observations received by IP. IP is preferred automatically;
LoRaWAN remains the low-bandwidth fallback when that home connection is absent.

## Deploy through the established paths

Car changes go through the **Duet courier**. Update the home checkout and run the
normal home installer on caydenserver1 to install the new home HA service.
Configure its existing broker settings above. No car HA dashboard reset, new
OTAA join, key rotation or replacement BTT Pi OS is needed.

On the Duet:

```bash
cd ~/car-dashboard
git pull --ff-only && bash duet/update.sh cayden@172.16.42.2
```

On caydenserver1:

```bash
cd ~/car-dashboard
git pull --ff-only && bash install.sh home
```

After entering the broker settings locally, check the bridge on caydenserver1:

```bash
sudo systemctl status car-home-ha --no-pager
cat ~/.local/share/car-home-control/home-ha-status.json
```

An active service alone does not verify MQTT discovery in Home Assistant. Check
that MQTT's devices include the car roles and that an actual received sample
updates its **Last reported** entity and observation timestamp.
