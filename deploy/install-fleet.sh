#!/usr/bin/env bash
# Automatically called by the normal Pi/Tiny/Duet installers.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run the device installer as your normal user.' >&2; exit 1; }
ROLE="${1:?Device role required: pi, tiny or duet}"
[[ "$ROLE" == pi || "$ROLE" == tiny || "$ROLE" == duet ]] || exit 2
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
BOOTSTRAP=/usr/local/lib/car-dashboard/fleet-bootstrap
sudo install -d -m 0755 "$BOOTSTRAP"
sudo install -o root -g root -m 0644 "$ROOT/deploy/fleet.py" "$BOOTSTRAP/fleet.py"
sudo install -o root -g root -m 0644 "$ROOT/power/wake.py" "$ROOT/power/standby.py" "$BOOTSTRAP/"
COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
sudo /usr/bin/python3 -I "$BOOTSTRAP/fleet.py" install --role "$ROLE" --repo "$ROOT" --user "$(id -un)" --source "$BOOTSTRAP" --commit "$COMMIT"
