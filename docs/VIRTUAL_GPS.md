# Virtual serial GPS source

`recorder/virtual_gps.py` is an external device simulator. It creates a Linux
pseudo-terminal and emits checksummed NMEA RMC/GGA/GSA sentences at 1 Hz, using
current UTC timestamps and a moving circular route. The real `gpsd` daemon reads
that serial device and parses the sentences. No GPS JSON, database rows, HA
entities or browser values are fabricated by the simulator.

```text
Physical GPS serial port OR virtual NMEA serial port
                         ↓
                     real gpsd
                         ↓
          normal recorder + reporting API
                         ↓
         history/replay + normal HA entities/cards
```

The application has no simulation flag or alternate rendering path. `gpsd` will
identify the device as a generic NMEA source; the OS path is a PTY, not a USB
hardware identity. This tests the serial/protocol/data pipeline, not USB
plug-and-play enumeration, USB drivers, antennas or satellite reception. It does
not emulate a kernel USB device or forge a u-blox hardware identity.

## On the car Tiny

Normal Tiny installation and automatic role updates install the source tool at
`/usr/local/lib/car-dashboard/virtual_gps.py` and include the Ubuntu `gpsd` and
`gpsd-clients` dependencies. The source is always off unless explicitly started.
It does not automatically start on boot or switch away from the physical GPS.

Run in a terminal on the Tiny (SSH over Tailscale is fine):

```sh
sudo python3 /usr/local/lib/car-dashboard/virtual_gps.py \
  --replace-system-gpsd --seconds 300 \
  --latitude 39 --longitude -77 --radius 300 --speed 10
```

The controller remembers which system `gpsd` units were active, temporarily stops
them, starts a separate real `gpsd` on the ordinary localhost port 2947, and feeds
it a virtual serial GPS. Leave this terminal running. The existing recorder and
reporting service reconnect normally; no application settings or databases are
changed. `--speed` is metres/second; `--radius` is metres.

Use any centre coordinates you want. This is a geometric loop, not road-following
navigation. Ordinary GPS cards show the position. Ordinary map rendering still
needs installed map tiles and a reachable tile URL. No mock OBD values are
supplied by a GPS device.

**Generated positions are ordinary input:** the normal recorder saves them in
its current history database. Other configured consumers may transmit them,
including radio services. Use a disposable test installation if you don't want
those points in your car history. There is intentionally no application-side
filter that detects and excludes them.

The tool restores previously active system `gpsd` units after five minutes,
Ctrl+C, SIGTERM, SSH hangup, or a handled error. The physical receiver must still have a fix
for valid positions after restoration. Persistent service configuration and
physical serial-port settings are left alone. An uncatchable SIGKILL cannot run
cleanup; if necessary recover with:

```sh
sudo systemctl restart gpsd.socket gpsd.service
```

For an immediate test from an updated checkout before the next role installation,
use `sudo python3 recorder/virtual_gps.py` with the same options. This does not
replace or modify the privileged installer or its trusted hashes.

## Separate Linux/WSL test session

Install `gpsd`, then run the source on a computer without a GPS service already
using port 2947:

```sh
sudo apt install gpsd gpsd-clients
python3 recorder/virtual_gps.py --seconds 300
```

If the package started a system GPS service, use the explicit
`--replace-system-gpsd` option with sudo. Do not run multiple source controllers.

In a separate terminal, the unmodified recorder can use an isolated database:

```sh
CAR_GPS_DB=/tmp/car-gps-test/gps.sqlite3 python3 recorder/gps_recorder.py
```

`gpspipe -w` and normal reporting endpoints can then inspect the fixes. A WSL
Car Assistant Docker preview must also have its reporting backend connected;
host `gpsd` does not automatically become reachable from a container's loopback.
This change does not silently attach the preview container to host hardware.

For a serial device without managing `gpsd`, use `--serial-only`. It prints a
`/dev/pts/...` device that another standard serial client or `gpsd` can open.
`--port` permits a separate gpsd instance for external test harnesses; production
clients still use their normal port 2947.

## Validation

Unit checks cover NMEA checksums, hemispheres, date/coordinate formatting and
restoring previously active services after failure. The optional real-gpsd test
starts the actual daemon on loopback, runs the unchanged recorder into a
disposable SQLite database, and reads moving fixes through the ordinary HA HTTP
adapter. It never changes the host system GPS service.

## Feed the laptop's browser location into the car Tiny

Update the Tiny first, then start the external source there:

```sh
sudo python3 /usr/local/lib/car-dashboard/virtual_gps.py \
  --replace-system-gpsd --browser --seconds 1800
```

It prints a **Laptop browser URL** containing a temporary token. On the laptop
where you want location, open another terminal and forward the source port
through your existing SSH/Tailscale access to the **car Tiny**:

```sh
ssh -N -L 8790:127.0.0.1:8790 cayden@CAR_TINY_TAILSCALE_IP
```

Leave both terminals running. Open the exact printed `http://127.0.0.1:8790/`
URL including its `#token=...` fragment **in that laptop's browser**, then click
**Share this laptop's location** and approve location access. OS location services
must be enabled. Accuracy depends on the laptop's GPS/Wi-Fi/OS provider; this
cannot create physical GNSS hardware or improve the provider's precision.

Coordinates go through an authenticated loopback-only receiver on the Tiny,
then standard NMEA → real gpsd → ordinary recording/reporting/HA. The HA page
itself never accesses geolocation or manufactures entities. Open the normal car
dashboard alongside the separate source page. A browser error, closing the source
page, or an old position causes a no-fix NMEA status after at most 30 seconds;
the emitter never keeps refreshing an old position's timestamp. **Stop sharing**
sends an immediate no-fix; Ctrl+C in the Tiny's source terminal restores physical
GPS. The 1800-second limit restores it automatically after 30 minutes.

Positions go into ordinary trip history. Keep the token private; it only lasts
for this source process. No port is opened on the car Ethernet/Tailscale addresses.
Keep the same local forwarding port (8790), since browser-origin checks include
that port. This source does not grant remote command execution or HA access.

### Ubuntu AppArmor

Some Ubuntu installations confine `/usr/sbin/gpsd` and deny pseudo-terminal
access. When that profile is present, the root source controller uses its
standard `local/usr.sbin.gpsd` include to add only `owner /dev/pts/N rw,` for the
specific PTY it created, then reloads the profile. It removes its exact block
and reloads again on normal exit, a handled failure, or termination. Existing
local rules and administrator edits are preserved. It does not disable AppArmor,
allow all terminals, or add ptrace access to other services.

The source controller needs sudo and `apparmor_parser` on confined systems. It
fails rather than replace an unfamiliar profile without the standard local
include. If an uncatchable SIGKILL or power loss leaves a block marked
`car-virtual-gps` in `/etc/apparmor.d/local/usr.sbin.gpsd`, inspect and remove that
block, then reload `/etc/apparmor.d/usr.sbin.gpsd` with `apparmor_parser -r` before
starting another session. Normal source termination performs that cleanup.

## Dashboard GPS state and maps

Drive Console's map, header and connection widget use the same valid tracker
position. A renamed/missing GPS binary-sensor entity does not independently make
the header report a disconnection while coordinates are present. Once the
tracker becomes unavailable, those widgets all report no GPS fix.

Local map tiles are fetched through an authenticated HA endpoint, so the existing
HA SSH tunnel also carries map imagery; an extra localhost:8877 tunnel is no
longer required for default tile access. Custom tile URLs remain supported.

When local tiles are missing, the map card defaults to online OpenStreetMap tiles
with visible attribution and an online status. This requires Internet on the
browser computer and requests the visible map area from OpenStreetMap. Disable
**Online fallback** in the map card editor to keep map imagery local-only. No
bulk tile downloader or offline pack is installed by this feature. Missing tiles
are reported independently of GPS status and are retried after 15 seconds.

The laptop source page also requests a fresh location every 10 seconds while
sharing. It never re-labels an old fix as current: if the provider stops supplying
fresh positions, the NMEA source expires the position normally.
