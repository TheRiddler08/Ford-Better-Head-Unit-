#!/usr/bin/env bash
# Automatically called by the normal Pi/Tiny/Duet installers.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run the device installer as your normal user.' >&2; exit 1; }
ROLE="${1:?Device role required: pi, tiny, duet or laptop}"
[[ "$ROLE" == pi || "$ROLE" == tiny || "$ROLE" == duet || "$ROLE" == laptop ]] || exit 2
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
BOOTSTRAP=/usr/local/lib/car-dashboard/fleet-bootstrap
COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
# One elevation even on systems whose sudo timestamp cache is disabled.
sudo /bin/bash -s -- "$ROOT" "$ROLE" "$(id -un)" "$COMMIT" "$BOOTSTRAP" <<'ROOTSETUP'
set -euo pipefail
ROOT="$1"; ROLE="$2"; USER_NAME="$3"; COMMIT="$4"; BOOTSTRAP="$5"
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
install -d -m 0755 "$BOOTSTRAP"
install -o root -g root -m 0644 "$ROOT/deploy/fleet.py" "$BOOTSTRAP/fleet.py"
install -o root -g root -m 0644 "$ROOT/deploy/wifi_profiles.py" "$ROOT/deploy/native_firewall.py" "$BOOTSTRAP/"
install -o root -g root -m 0644 "$ROOT/power/wake.py" "$ROOT/power/standby.py" "$BOOTSTRAP/"
exec /usr/bin/python3 -I "$BOOTSTRAP/fleet.py" install --role "$ROLE" --repo "$ROOT" --user "$USER_NAME" --source "$BOOTSTRAP" --commit "$COMMIT"
ROOTSETUP
