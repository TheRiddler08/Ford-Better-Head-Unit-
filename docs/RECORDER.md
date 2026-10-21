# OBD2 recorder integration

This repository includes **two separate programs**:

- `app/server.py`: web dashboard, reads SQLite only.
- `recorder/obd_full_recorder.py`: reads the USB OBD2 adapter and writes SQLite.

The recorder is a newly implemented compatible version: **it is not the exact original Python source** currently on your car computer. The existing recorder remains unchanged until you deliberately adopt the new version.

## Adopt recorder (once, with FORScan closed)

First back up any local customizations:

```bash
cp ~/car-console/obd-test/obd_full_recorder.py ~/car-console/obd-test/obd_full_recorder.py.backup
```

Then:

```bash
cd ~/car-dashboard
bash deploy/install-recorder.sh
```

This preserves `~/car-console/obd-data/vehicle.sqlite3`, saves the previous `car-obd-monitor.service` unit into `/var/backups/car-dashboard/`, and switches that one systemd service to GitHub's recorder. The recorder uses python-OBD in the repository's separate `.venv`. Only read-only OBD requests from standard modes 01/02/03/06/07/09 are allowed, excluding VIN.

The dashboard and recorder remain separate systemd services. Never run FORScan and the recorder at the same time.

Check:

```bash
sudo systemctl status car-obd-monitor --no-pager
sudo journalctl -u car-obd-monitor -n 60 --no-pager
```

If your existing recorder has special behavior, compare the old and new outputs before adopting. In particular, the new recorder has its own read-only polling and snapshot scheduler. GitHub updates can automatically deploy code to both only after adoption.
