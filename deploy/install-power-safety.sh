#!/usr/bin/env bash
# Tiny only. Reduces dirty writes; does not promise safe instantaneous power loss.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as your ordinary Tiny user.' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
command -v systemctl >/dev/null || { echo 'systemd is required.' >&2; exit 1; }
[[ -x /usr/bin/sync ]] || { echo '/usr/bin/sync is required.' >&2; exit 1; }
BASE=/usr/local/lib/car-dashboard/power-safety
sudo install -d -o root -g root -m 0755 "$BASE"
sudo install -o root -g root -m 0644 "$ROOT/deploy/offline_payload.py" "$ROOT/deploy/runtime_assets.py" "$BASE/"
sudo /usr/bin/python3 -I "$BASE/offline_payload.py" --install-power-safety
echo 'Disk flush timer enabled every 15 seconds. Recorder durability activates when managed recorders restart.'
echo 'An ignition-aware power controller/UPS is still needed for a reliable clean shutdown.'
