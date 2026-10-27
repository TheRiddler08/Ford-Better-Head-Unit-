#!/usr/bin/env bash
# Tiny only. Uses an already configured localhost gpsd; never opens the OBD port.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the Tiny dashboard user, not root.' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
case "$ROOT" in *' '*|*'%'*) echo 'Repository path must not contain spaces or %.' >&2; exit 1;; esac
DB="${CAR_GPS_DB:-$HOME/car-console/obd-data/gps.sqlite3}"
python3 -m py_compile "$ROOT/recorder/gps_recorder.py"
mkdir -p "$(dirname "$DB")"
sudo tee /etc/systemd/system/car-gps-recorder.service >/dev/null <<UNIT
[Unit]
Description=Persistent local GPS movement history
After=network.target gpsd.service
[Service]
User=$(id -un)
WorkingDirectory=$ROOT
ExecStart=/usr/bin/python3 -u $ROOT/recorder/gps_recorder.py --db $DB
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now car-gps-recorder.service
echo "GPS journal: $DB"
echo 'Configure the USB GPS receiver in gpsd separately. Do not point gpsd at the OBD adapter.'
echo 'Set CAR_GPS_DB in dashboard.env to this same path if using a nondefault path.'
