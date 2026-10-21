# Ford Better Head Unit

An evolving **Ubuntu-based custom vehicle head unit** for the Lenovo ThinkCentre M910q Tiny. This repository currently ships the **read-only OBD2 telemetry dashboard**, which works with the user's existing SQLite recorder; navigation, GPS, CarPlay, dashcam, and FORScan expansion are planned separately.

## What works today

- Browser dashboard: current speed, RPM, coolant temperature, electrical voltage and historical trends
- All recorded sensor readings, diagnostic snapshots, recorder events, estimated trip sessions and fuel-level anomaly notices
- Python-standard-library HTTP server, responsive offline-first frontend; no npm/Python packages or cloud dependencies
- Read-only access to the existing SQLite database — never opens or controls the USB OBD2 adapter
- systemd auto-start at boot and **optional GitHub auto-updates** with fast-forward-only changes, automated tests, health checks and rollback
- GitHub Actions CI

**Not implemented yet:** GPS navigation, offline maps, iPhone CarPlay, FORScan-specific Ford modules, dashcam integration, OBD recorder installation or modification. The existing recorder must be installed separately.

## Install on the car computer (Ubuntu)

On the **Lenovo Ubuntu terminal**, logged in as `cayden` (not root):

```bash
sudo apt update
sudo apt install -y git python3 curl
git clone https://github.com/TheRiddler08/Ford-Better-Head-Unit-.git ~/car-dashboard
cd ~/car-dashboard
python3 -m unittest discover -s tests -v
bash deploy/install.sh
```

Open **http://127.0.0.1:8787/** in a browser running on the Lenovo.

The installer enables `car-dashboard.service`; it does not modify `car-obd-monitor.service`, the car, or the recorder database.

```bash
sudo systemctl status car-dashboard --no-pager
sudo journalctl -u car-dashboard -n 50 --no-pager
curl -fsS http://127.0.0.1:8787/api/health
curl -fsS http://127.0.0.1:8787/api/summary
```

The default database is `/home/cayden/car-console/obd-data/vehicle.sqlite3`. Change it in `/etc/car-dashboard/dashboard.env` if needed, then restart:

```bash
sudo nano /etc/car-dashboard/dashboard.env
sudo systemctl restart car-dashboard
```

The config has three keys: `CAR_DB`, `CAR_BIND` (default `127.0.0.1`), and `CAR_PORT` (default `8787`).

## GitHub auto-updates (opt in once)

After installing from the clone, run as `cayden`:

```bash
cd ~/car-dashboard
bash deploy/enable-updates.sh
```

The installer will ask for confirmation. When enabled, a systemd timer periodically fetches the selected **origin branch** and deploys a fast-forward update only if the working tree is clean and the automated tests pass. On a failed health check, the updater rolls back the dashboard code. It only restarts the **dashboard**, never the OBD recorder.

```bash
systemctl list-timers car-dashboard-update.timer
sudo journalctl -u car-dashboard-update.service -n 60 --no-pager
sudo systemctl start car-dashboard-update.service    # check manually now
bash deploy/disable-updates.sh
```

Auto-update code changes on the configured branch run on your car PC. **Require reviewed pull requests and protect that branch; never store vehicle data, passwords, SSH keys, tokens, or `*.sqlite3` files in Git.** On-device software is not a substitute for the factory gauges.

## Access remotely

The dashboard binds to localhost and has **no login system**. Avoid exposing it on the public Internet. You can tunnel access over SSH/Tailscale:

```bash
ssh -L 8787:127.0.0.1:8787 cayden@YOUR_CAR_TAILSCALE_ADDRESS
```

Then open http://127.0.0.1:8787 on the machine running that SSH command.

## Existing recorder database

Expected columns:

- `readings(ts, name, number, value, unit)`
- `snapshots(id, stage, started, total, successes)`
- `events(ts, name, detail)`

The last two tables are optional. Telemetry timestamps are Unix seconds. The dashboard opens SQLite with `mode=ro` and `PRAGMA query_only=ON` and does not open `/dev/ttyUSB0` or modify the recorder service. The live gauges show **last recorded values** with a freshness indicator, not guaranteed current vehicle conditions. Speed-history-derived mileage and trip boundaries are estimates.

### API

`/api/health`, `/api/live`, `/api/summary`, `/api/sensors`, `/api/history?name=SPEED&period=24h`, `/api/trips`, `/api/snapshots`, `/api/events`, `/api/alerts`.

## Development

```bash
python3 -m unittest discover -s tests -v
CAR_DB=/path/to/sample.sqlite3 python3 app/server.py
```

No third-party dependencies are required for the dashboard. The update timer needs Git and standard Ubuntu systemd utilities.

See [SECURITY.md](SECURITY.md) for operational cautions.
