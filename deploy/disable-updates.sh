#!/usr/bin/env bash
set -euo pipefail
sudo systemctl disable --now car-dashboard-update.timer
printf 'Disabled dashboard updates (dashboard itself is unchanged).\n'

sudo install -d -m 0755 /etc/car-dashboard
sudo touch /etc/car-dashboard/updates-disabled
