#!/usr/bin/env bash
set -euo pipefail
sudo systemctl disable --now car-dashboard-update.timer
printf 'Disabled dashboard updates (dashboard itself is unchanged).\n'
