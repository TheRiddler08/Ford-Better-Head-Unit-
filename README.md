# Drive Console · Car Head Unit

An offline-capable, read-only web interface for the existing Ubuntu/Python OBD2 recorder. Designed for a Lenovo ThinkCentre M910q with a 2016 Ford and a CH340 USB ELM327 adapter, but it only reads SQLite and can also run without an adapter attached.

![Cockpit](https://img.shields.io/badge/touchscreen_UI-v2.0-55e6c1) ![Dependencies](https://img.shields.io/badge/python-stdlib_only-blue) ![Privacy](https://img.shields.io/badge/data-local_only-lightgrey)

## Touchscreen cockpit v2

The default home screen now uses a purpose-built car-console layout optimized for the Lenovo's touch display and for 16:9 landscape screens:

- Large circular speed/RPM indicators with **individual measurement timestamps**; readings are clearly marked stale when the separate recorder stops.
- Big coolant, ECU voltage, and **unverified raw fuel percentage** cards, plus engine load and intake temperature when available.
- Interactive sensor timeline, stored readings, driving-session history and diagnostics remain available through the left navigation rail (bottom navigation on small screens).
- Full-screen toggle, automatic time/date, high-contrast **day/night appearance toggle**, and US/metric unit selection.
- Entirely local CSS, SVG and JavaScript with no third-party scripts, mapping API, analytics, web fonts or network connection needed to render. The data still comes from the existing local SQLite recorder.
- No fabricated maps, media, climate controls, CarPlay or Ford controls: those require real hardware/service integrations later. The UI **never** writes to the ECU and must not be used as a substitute for factory instruments.

For touchscreens use the fullscreen kiosk provided by `deploy/install.sh`; existing kiosks auto-reload the page when the updater installs a new Git commit. Recheck or refresh readings manually with the button, or let it poll every three seconds.

## Features

- Responsive dark dashboard with MPH/km/h, RPM, engine coolant temperature, electrical system voltage
- Sensor timeline charts from the local database (1 hour to 30 days), no cloud services or chart CDN
- Searchable inventory of recorded sensors with sample counts and min/max/average
- Inferred driving sessions grouped by > 5 minute telemetry gaps, peak speed, RPM, sampled-speed-based distance estimates
- Deep diagnostic snapshots, recorder events and possible anomalous fuel-zero readings
- Read-only SQLite `mode=ro` with `PRAGMA query_only=ON`; **no** ELM327 serial access, writes, code clearing, Ford module control, VIN uploads or vehicle configuration changes
- Native Linux systemd service and opt-in 15-minute GitHub fast-forward auto-updates with tests, health checks and rollback

### Does not yet do

- GPS route mapping, CarPlay, driving scoring, reliable fuel economy or module-specific FORScan PID collection
- Guaranteed exact trip start/end, exact odometer distance or collision alerts
- User login/authentication (localhost-only by default)
- Modify, supervise or start the existing `car-obd-monitor.service`

## Existing database schema

Designed for the existing recorder file:

`/home/cayden/car-console/obd-data/vehicle.sqlite3`

Tables/columns from the actual command output:

- `readings`: `ts`, `name`, `value`, `number`, `unit`
- `snapshots`: `id`, `stage`, `started`, `total`, `successes`
- `events`: `ts`, `name`, `detail`

A missing optional table is tolerated. The `readings` table must be present. SQLite schema changes to this system may require adapting the SQL queries.

**Important:** The dashboard has not been tested against the user's actual database yet; use `/api/health` and the status page to validate it on the car PC.

## Optional wired Duet 3 touchscreen client

The Lenovo Chromebook Duet 3 (11Q727 / `google-wormdingler`, when running a
compatible Linux build) can display the Tiny's cockpit using **USB gadget
Ethernet**, not Wi-Fi or an HDMI cable. The Tiny remains the source of OBD,
GPS, navigation, maps, and updates. The Duet is a touchscreen/browser client
with a secure SSH tunnel over USB and an offline/reconnect screen.

See **[Wired Duet 3 setup](docs/DUET_USB_DISPLAY.md)** for prerequisites, USB
interface detection, host key verification, installation scripts and recovery.
Setup is opt-in and preserves the Tiny's existing dashboard and OBD services.
Do not change the dashboard's `CAR_BIND=127.0.0.1` setting.

## Setup on Ubuntu

Install on the Linux computer as `cayden` (never use `sudo git clone`):

```bash
sudo apt update && sudo apt install -y git python3
git clone https://github.com/TheRiddler08/Ford-Better-Head-Unit-.git ~/car-dashboard
cd ~/car-dashboard
bash deploy/install.sh
```

Installer configures both `car-dashboard.service` and `car-obd-monitor.service` and installs the recorder's Python OBD dependency. The recorder automatically creates a new database when needed and preserves existing vehicle history. An existing recorder service is backed up before replacement. **Close FORScan and ensure the USB OBD adapter is free before installation.**

At the next reboot, a regular install also opens the dashboard full-screen without showing the Ubuntu login screen. See [Boot-to-dashboard kiosk instructions](docs/KIOSK.md) for how it works, security considerations, disabling it, and manual setup. For backend-only installs, use `CAR_KIOSK=0 bash deploy/install.sh`.

Visit **http://127.0.0.1:8787/** through a browser *running on the Lenovo PC*.

If your GUI/physical display is unavailable, test the API through MeshCentral's terminal:

```bash
curl -s http://127.0.0.1:8787/api/health
curl -s http://127.0.0.1:8787/api/summary
sudo systemctl status car-dashboard --no-pager
sudo journalctl -u car-dashboard -n 100 --no-pager
```

For SSH access from your laptop without opening a public port:

```bash
ssh -L 8787:127.0.0.1:8787 cayden@YOUR-M910Q-TAILSCALE-IP
```

Then open `http://127.0.0.1:8787/` on **your laptop**. The SSH session stays open while browsing. Keep the app bound to loopback unless you've added separate access controls. Do not port-forward the unauthenticated dashboard to the public internet.

## Systemd service configuration

The installer creates `/etc/car-dashboard/dashboard.env` only when absent:

```ini
CAR_DB=/home/cayden/car-console/obd-data/vehicle.sqlite3
CAR_BIND=127.0.0.1
CAR_PORT=8787
```

After editing it, apply with `sudo systemctl restart car-dashboard`.

## Manual update (without enabling automatic updates)

Run from the existing Git clone as your normal Ubuntu login user:

```bash
cd ~/car-dashboard
bash deploy/manual-update.sh
```

This updates the dashboard and the repository-managed recorder together, performs the repository tests, and rolls back the source if a health check fails. It only restarts the recorder when its code changes and the recorder was already running. The SQLite history is preserved. For a new installation, use `bash deploy/install.sh` instead.

## Opt-in GitHub updates

This is deliberately **disabled by default**. After installing from this GitHub repository using a real `git clone`:

```bash
cd ~/car-dashboard
bash deploy/enable-updates.sh
```

On explicit `yes` confirmation, this installs:

- `car-dashboard-update.timer`: every 15 minutes, up to one-minute randomized delay, catch-up after power off
- `car-dashboard-update.service`: network-dependent one-shot update
- `/usr/local/sbin/car-dashboard-update`: a root-owned stable helper that updates the checked-out branch **as your ordinary Linux user** and restarts only `car-dashboard.service`

Updates use `git fetch` and `git merge --ff-only`, refusing rewritten history or local tracked file modifications. The updater runs Python unit tests and checks the HTTP/database health; failures roll the dashboard code back to its prior commit. Credentials, if any, are read from your user's existing git setup; do not embed personal access tokens in files. Updates run new committed Python dashboard code as your unprivileged user. **Anyone with push permissions to that selected branch can modify the software running in your car. Protect branch access and preferably require reviewed pull requests.**

To disable:

```bash
bash deploy/disable-updates.sh
```

To inspect a check without changing your repo, use `git -C ~/car-dashboard fetch origin` followed by `git -C ~/car-dashboard log HEAD..FETCH_HEAD --oneline`. To manually run the configured update: `sudo systemctl start car-dashboard-update.service`.

## Development

Zero Python dependencies. Start against any correctly structured SQLite database:

```bash
CAR_DB=/path/to/sample.sqlite3 CAR_BIND=127.0.0.1 CAR_PORT=8787 python3 app/server.py
python3 -m unittest discover -s tests -v
```

Architecture: `app/server.py` hosts static `app/web/` and JSON read-only endpoints:

| Endpoint | Description |
|---|---|
| `/api/health` | Dashboard process and database presence |
| `/api/live` | Most recent sensor values and freshness |
| `/api/summary` | Readings, sensor and snapshot totals |
| `/api/sensors` | Sensor sample counts and numerical aggregates |
| `/api/history?name=SPEED&period=24h` | 300-ish time-bucketed historical values |
| `/api/trips` | Grouped RPM/speed observation clusters |
| `/api/snapshots` | Last 60 diagnostic snapshot summaries |
| `/api/events` | Last 80 recorder events |
| `/api/alerts` | Fuel zero readings near conflicting non-zero measurements |

For large databases, the `trips` query and sensor grouping may become slow. Later versions can add an optimized recorder-maintained trip summary and indices after reviewing the real schema and write load. The dashboard itself will never add indices to the database.

### Security and data accuracy

- Read-only SQLite prevents this application's SQL connection from modifying your logger database, while other applications such as the existing recorder remain able to update it. It may create SQLite shared-memory/WAL support files when required; that's SQLite's concurrency behavior, not an application-initiated data write.
- The UI never communicates with the vehicle. It should not be used for safety-critical driving decisions or as a replacement for the factory gauges.
- GPS, driving direction, true odometer, and ignition boundaries are **not** in the present database. Sessions and distance are estimates; no route map is presented as if it were known.
- Fuel warning is a heuristic; 0% may be genuine and the warning cannot diagnose an electrical or fuel system fault.
- Localhost by default. Only expose to another device through an authenticated private tunnel such as SSH port forwarding or a controlled Tailscale Serve setup.

## License

MIT (`LICENSE`).


## GitHub-managed OBD2 recorder (separate service)

The same repository now contains `recorder/obd_full_recorder.py`, a standalone, read-only USB ELM327 recorder that writes to the existing `vehicle.sqlite3`. Its installer is intentionally separate and must be run once to adopt it, because your current recorder is already operational. **The original local script source is not in this repository, so the new recorder is a compatible replacement rather than the exact same code.** See [recorder migration instructions](docs/RECORDER.md).

```bash
cd ~/car-dashboard
bash deploy/install-recorder.sh
```

After adoption, the GitHub updater will check both pieces of software. Updates always restart the dashboard; when recorder code changes, the updater restarts the recorder only if it was already running. If you've stopped it to use FORScan, the updater leaves it stopped. Both have separate systemd services.

## Home-to-car settings over Tailscale

Use the [home server and car installation guide](docs/TAILSCALE_HOME_CONTROL.md) to deliver layout and settings changes over an authenticated SSH tunnel on your tailnet. LoRaWAN is optional. Software updates continue through the existing GitHub updater.

## Visual UI builder and source editor

The home panel includes a multi-page drag-and-resize canvas, component inspector, HTML/CSS/JavaScript editors, sandboxed live preview, draft saving, project import/export, and versioned publishing over Tailscale. [Builder usage and upgrade steps](docs/UI_BUILDER.md). The original dashboard remains available for recovery.
