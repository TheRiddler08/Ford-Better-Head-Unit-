#!/usr/bin/env bash
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo "Run as normal user" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
USER_NAME="$(id -un)"
DB="${CAR_DB:-$HOME/car-console/obd-data/vehicle.sqlite3}"
mkdir -p "$(dirname "$DB")"
echo "Installing the managed OBD recorder, preserving existing SQLite history."
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip
[[ -x "$ROOT/.venv/bin/python" ]] || python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install 'obd==0.7.3'
"$ROOT/.venv/bin/python" -m py_compile "$ROOT/recorder/obd_full_recorder.py"
if systemctl cat car-obd-monitor.service >/dev/null 2>&1; then
 sudo install -d -m 0700 /var/backups/car-dashboard
 sudo systemctl cat car-obd-monitor.service | sudo tee "/var/backups/car-dashboard/car-obd-monitor.$(date +%Y%m%d-%H%M%S).txt" >/dev/null
 sudo systemctl stop car-obd-monitor.service
fi
sudo tee /etc/systemd/system/car-obd-monitor.service >/dev/null <<EOF
[Unit]
Description=Read-only Ford OBD2 recorder from GitHub
After=systemd-udev-settle.service
Wants=systemd-udev-settle.service

[Service]
Type=simple
User=$USER_NAME
SupplementaryGroups=dialout
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
Environment=CAR_DB=$DB
Environment=OBD_PORT=${OBD_PORT:-/dev/ttyUSB0}
ExecStart=$ROOT/.venv/bin/python -u $ROOT/recorder/obd_full_recorder.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=40
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now car-obd-monitor.service
echo "Managed recorder enabled; SQLite history not deleted."
