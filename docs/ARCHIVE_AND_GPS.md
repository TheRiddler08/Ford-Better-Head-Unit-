# Detailed vehicle archive and persistent GPS routes

Open **Reports & GPS history** from the car dashboard (or `/records` through the
Duet's existing proxy). The full archive stays local and read-only. The optional configured radio link
now queues selected encrypted GPS samples and camera catalog details for home;
see [storage and radio privacy](CAR_STORAGE_AND_RADIO_PRIVACY.md). No online map
provider receives the archive.

## What is retained

- Every successful sensor reading remains in the existing `readings` table.
- The managed recorder additionally journals every OBD query it attempts: command
  name and hexadecimal request, start/end timestamps, elapsed milliseconds,
  decoded value/unit, success/empty/error/rejected status, error detail, available
  response bytes, and the diagnostic scan ID when applicable.
- Existing snapshot readings, scan summaries and recorder events are reportable.
- A separate GPS service records real gpsd fixes and all accepted GPS messages,
  including messages with no position fix, plus connection/disconnection events.
- Every valid position includes receipt time, GPS time (or an explicitly labelled
  receipt-time fallback), coordinates, altitude where available, speed in m/s,
  heading, fix mode and horizontal error estimates when the receiver provides them.

The request allowlist remains read-only. No ECU writes, clear codes, VIN reads,
actuator controls or arbitrary manufacturer commands are enabled. This cannot
collect unsupported PIDs, recover values never sampled, recover raw bytes from
older readings, or reconstruct trips before GPS recording was installed. Ordinary
fast sensors retain their existing polling schedule; supported-command diagnostic
snapshots run at connection, after five minutes and hourly.

## Using reports

Select a record type, optional local start/end dates, sensor/command name, outcome
or scan ID. **Every sensor value** reads historical legacy data too. The table is
cursor-paginated without time bucketing or averaging. **Load next 500 records**
continues without dropping intermediate rows. Click a row for complete fields,
including response bytes and errors. **Export all matching CSV** exports every
matching record across all pages; the active date/command filters apply.

CSV is raw data, not a trusted spreadsheet workbook. Use a text/CSV data importer
that does not execute formula-like text in untrusted fields.

## USB GPS setup — Tiny only

Run these on the **Tiny connected to the USB GPS**, not on the Duet or home server.
Install gpsd once while the Tiny has access to distribution packages:

```sh
sudo apt update
sudo apt install -y gpsd gpsd-clients
ls -l /dev/serial/by-id/
```

Identify the GPS receiver's persistent device name. **Do not use the CH340/ELM327
OBD adapter's serial port.** Edit the existing gpsd configuration, preserving
unrelated settings:

```sh
sudoedit /etc/default/gpsd
```

On Ubuntu/Debian set the receiver's actual path, for example:

```ini
DEVICES="/dev/serial/by-id/REPLACE_WITH_YOUR_GPS_RECEIVER"
GPSD_OPTIONS="-n"
USBAUTO="false"
```

Do not enable gpsd's `-G` option: the application uses localhost port 2947 only.
Then:

```sh
sudo systemctl restart gpsd.socket gpsd.service
cgps -s
```

Test outdoors or near a window until a real 2D/3D fix appears; exit `cgps` with `q`.
Once the updated repository is on the Tiny:

```sh
cd ~/car-dashboard
bash deploy/install-gps-recorder.sh
sudo systemctl status car-gps-recorder --no-pager
sudo journalctl -u car-gps-recorder -n 40 --no-pager
```

The journal defaults to `~/car-console/obd-data/gps.sqlite3`. The installer runs
without changing USB networking, OBD services or gpsd configuration. It reconnects
when gpsd is unavailable and creates no invented coordinates while waiting.

The dashboard defaults to `gps.sqlite3` beside its `CAR_DB`. If your recorder and
dashboard use another path, run the installer with `CAR_GPS_DB=/actual/path` and
set that same `CAR_GPS_DB` in `/etc/car-dashboard/dashboard.env`, then restart the
dashboard. The service should run as the normal dashboard user.

The OBD request journal requires the repository-managed `car-obd-monitor` service.
Existing installations of that recorder gain the journal after updating/restarting
it. If still using an independent legacy recorder, follow [recorder adoption](RECORDER.md)
once; do not run two recorders against the USB adapter.

## Route history and visited places

The route section uses the same date range filters. **Load route** retrieves the
first 2,000 actual fixes; its status explicitly says when history is partial.
Continue with the next page or **Load all matching fixes**. Every loaded fix is
a path vertex. Drag to pan, use scroll/zoom buttons, and click the path to inspect
the nearest actual coordinate and its time, speed and precision fields. CSV export
of GPS fixes retains every stored field regardless of what is currently plotted.

Recording starts a new segment after gaps over 120 seconds, backwards clock
changes, crossing the date line, and implausible position jumps (over the larger
of 100 metres or 120 m/s times elapsed time). It never draws a connecting line
across those boundaries. GPS errors can still occur; the plotted path shows fixes,
not a road-snapped reconstruction or proof of every physical movement.

**Visited stops** estimates stops where fixes remain within 75 metres of an anchor
for at least three minutes, without a recording gap. They are coordinate/dwell-time
records, not automatic street addresses. A drifting or poor fix can affect them.

The coordinate plot works without Internet or map assets. Enable **Show installed
offline map tiles** only if you have XYZ PNG tiles in the configured `CAR_MAP_TILES`
folder (default `/var/lib/car-dashboard/tiles`). No online tile service, geocoder
or map subscription is contacted. Missing tiles leave the coordinate path visible.

## Retention and backups

There is no automatic deletion or downsampling of the archive. Disk space is
finite: monitor the data directory and back it up. GPS raw messages and OBD request
logs add storage beyond the old sensor table. GPS uses SQLite WAL; do not copy just
the live main file and assume it contains its WAL. Use SQLite's online backup API
or the SQLite CLI `.backup` command for both databases, and protect backups like
private vehicle/location history. Downloaded software courier packages contain
Git source, not these databases.

## Automatic recorder and replay

Normal Tiny installation and verified role updates now enable the GPS recorder
by default. Existing stopped/masked services and `gps_recording: false` in the
root-owned fleet configuration are preserved. The recorder reads the dashboard's
`CAR_DB` / `CAR_GPS_DB` environment, runs as the dashboard account and reconnects
to localhost gpsd automatically. It does not claim an unidentified USB serial
port: receiver provisioning remains pending until the GPS receiver is identified
and gpsd provides real fixes. Do not give gpsd the OBD adapter.

The archive additionally retains every byte received from the **gpsd JSON
socket**, base64-encoded by chunk, with receipt timestamp and connection session.
This includes malformed JSON and incomplete messages; it is not USB wire capture.
The stream and derived fixes commit together. A lost GPS fix or new connection
starts a new segment, even during short outages. An unclosed previous session is
marked interrupted on reconnection (its end is detection time, not exact outage).

Load a route and use Play/pause, speed and the position slider to replay its
recorded timestamps. Replay is limited to loaded fixes; load further pages or all
matching fixes for a longer trip. Segment breaks are skipped after a visible pause,
without creating intermediate positions. Dates, coordinates and complete fix
fields remain available while replaying.

## Fixed-gateway coverage evidence

The authenticated home control panel includes a coverage plot and audit totals.
After decryption and application authentication, the home bridge converts a GPS
status message into this internal object for coverage gating. An unencrypted
ChirpStack decoder object is not accepted as evidence:

```json
{"carGpsV1":{"lat":40.0,"lon":-75.0,"ts":1793100000,
 "gatewayMode":"client","gatewayModeAt":1793100000}}
```

The MQTT event must contain a stable `deduplicationId` and `rxInfo` gateway IDs.
Configure `gateways.json` in the home control state directory (default
`~/.local/share/car-home-control`) using actual lowercase gateway IDs:

```json
{"actual-fixed-gateway-id":{"role":"fixed"},
 "actual-vehicle-gateway-id":{"role":"mobile"}}
```

Only a fixed registered receiver plus an explicitly known client-only vehicle
role, a mode observation within 30 seconds of its GPS sample, and a GPS sample
received within five minutes can contribute. Mobile/unknown/stale observations
remain in the audit database but cannot contribute to coverage. Duplicate MQTT
events are idempotent. Classify the entire vehicle as gateway-active while its
future concentrator forwards traffic, even if the RAK3172 end device also runs.

Reception points and sampled 0.001-degree cells are evidence, not guaranteed RF
circles. Unheard packets, stale GPS, backhaul failures and untested areas are not
negative RF measurements. No synthetic boundary is drawn across untested areas.
The archive is persistent and cursor-paginated, with no automatic deletion.

This adds the evidence consumer, audit and map; **it does not configure a real
RAK3172 modem, install ChirpStack, or detect the
future RAK5146 role yet**. The car now queues encrypted sampled GPS telemetry, with gateway mode explicitly
unknown. Until the hardware modem and fresh gateway-role reporting are connected,
the coverage plot correctly stays empty. Do not mistake a configured
MQTT connection or a queued message for validated over-air delivery. Phase 1 uses
the RAK3172-E on the Pi, Tiny USB GPS and a US915 fixed gateway; the future
RAK5146 is a separate radio role. Match US915 sub-band/data-rate payload limits
before enabling transmissions. Large GPS backlogs belong on Wi-Fi/Ethernet.
