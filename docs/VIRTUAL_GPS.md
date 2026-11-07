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
