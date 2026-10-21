#!/usr/bin/env bash
# Run as cayden: bash deploy/install.sh
set -euo pipefail
if [[ "$EUID" == 0 ]]; then
  echo 'Run this script as your ordinary Ubuntu user, not root (sudo will be requested where needed).' >&2
  exit 1
fi
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 1; }
command -v systemctl >/dev/null || { echo 'systemd is required.' >&2; exit 1; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
DB_DEFAULT="$HOME/car-console/obd-data/vehicle.sqlite3"
DB="${CAR_DB:-$DB_DEFAULT}"
python3 -m py_compile "$ROOT/app/server.py"
if [[ ! -f "$DB" ]]; then
  echo "WARNING: database not found yet at $DB" >&2
  echo 'You may need to adjust CAR_DB in /etc/car-dashboard/dashboard.env.' >&2
fi
if [[ ! -r "$DB" ]] && [[ -f "$DB" ]]; then
  echo "ERROR: user $USER_NAME cannot read $DB" >&2
  exit 1
fi
sudo install -d -m 0755 /etc/car-dashboard
if [[ ! -f /etc/car-dashboard/dashboard.env ]]; then
  printf 'CAR_DB=%s\nCAR_BIND=127.0.0.1\nCAR_PORT=8787\n' "$DB" | sudo tee /etc/car-dashboard/dashboard.env >/dev/null
  sudo chmod 0644 /etc/car-dashboard/dashboard.env
else
  echo 'Keeping existing /etc/car-dashboard/dashboard.env'
fi
# Properly escape spaces for systemd ExecStart paths; % is deliberately unsupported.
if [[ "$ROOT" == *' '* || "$ROOT" == *'%'* ]]; then
  echo "Please install under a path with no spaces or % characters: $ROOT" >&2
  exit 1
fi
sudo tee /etc/systemd/system/car-dashboard.service >/dev/null <<EOF
[Unit]
Description=Local read-only car telemetry dashboard
After=network.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$ROOT
EnvironmentFile=/etc/car-dashboard/dashboard.env
ExecStart=/usr/bin/python3 $ROOT/app/server.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now car-dashboard.service
printf '\nDashboard installed: http://127.0.0.1:8787\n'
printf 'Service status: sudo systemctl status car-dashboard --no-pager\n'
printf 'OBD recorder service was not changed.\n'
printf 'GitHub auto-updates are OFF. Run bash deploy/enable-updates.sh after pushing the repository.\n'
