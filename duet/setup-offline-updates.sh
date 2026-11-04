#!/usr/bin/env bash
# Run once on Duet with Tiny attached. Only Duet ever needs Internet.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the normal Duet user, not root.' >&2; exit 1; }
TARGET="${1:-cayden@172.16.42.2}"
[[ "$TARGET" =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+$ ]] || { echo 'Expected Tiny user@host' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
# This is your normal administrative SSH login, not the display-only key.
# An agent may offer our forced-command display/courier keys first. They can
# authenticate successfully yet reject mkdir/scp/sudo with a silent exit 1.
# Never reuse an unrelated display/courier session. A private, short-lived
# administrative master reuses this one login for mkdir, scp and installation.
CONTROL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/car-pair-admin.XXXXXXXX")"
ADMIN_SSH=(-o IdentityAgent=none -o ControlMaster=auto -o "ControlPath=$CONTROL_DIR/ssh" -o ControlPersist=600 -o BatchMode=no)
cleanup() {
 if [[ -S "$CONTROL_DIR/ssh" ]]; then ssh "${ADMIN_SSH[@]}" -O exit "$TARGET" >/dev/null 2>&1 || true; fi
 rm -rf -- "$CONTROL_DIR"
}
trap cleanup EXIT
if [[ -n "${CAR_ADMIN_IDENTITY:-}" ]]; then
 [[ -f "$CAR_ADMIN_IDENTITY" ]] || { echo 'CAR_ADMIN_IDENTITY must name your normal administrative private key.' >&2; exit 1; }
 ADMIN_SSH+=(-o IdentitiesOnly=yes -i "$CAR_ADMIN_IDENTITY")
else
 ADMIN_SSH+=(-o PubkeyAuthentication=no -o PreferredAuthentications=keyboard-interactive,password)
fi
STEP='administrative SSH login'
trap 'code=$?; printf "Pairing stopped during %s (exit %s). Downloads are not cancelled.\n" "$STEP" "$code" >&2; exit "$code"' ERR
echo "Pairing with $TARGET using administrative authentication (not the display/courier keys)."
# Keep normal host-key verification; never accept a changed fingerprint blindly.
ssh "${ADMIN_SSH[@]}" "$TARGET" 'mkdir -p "$HOME/car-courier-bootstrap"'
# Create/install the unprivileged Duet worker and its separate signing key.
BASE="$HOME/.local/share/drive-console"
if [[ -f "$HOME/.ssh/drive-console-courier.pub" ]] && python3 - "$ROOT/deploy" "$BASE/courier-current" <<'PYREADY'
from pathlib import Path
import sys
source,current=map(Path,sys.argv[1:])
try:ready=all((source/name).read_bytes()==(current/name).read_bytes() for name in ('offline_updates.py','runtime_assets.py','offline_payload.py','home_assistant.py'))
except OSError:ready=False
raise SystemExit(0 if ready else 1)
PYREADY
then
 echo 'Duet worker is already installed; preserving the active download.'
else
 bash "$ROOT/duet/install-courier.sh" "$TARGET"
fi
STEP='copying receiver bootstrap'
scp "${ADMIN_SSH[@]}" "$ROOT/deploy/install-courier.sh" "$ROOT/deploy/offline_updates.py" \
 "$ROOT/deploy/runtime_assets.py" "$ROOT/deploy/offline_payload.py" "$ROOT/deploy/home_assistant.py" \
 "$ROOT/deploy/fleet.py" "$ROOT/power/wake.py" "$ROOT/power/standby.py" \
 "$HOME/.ssh/drive-console-courier.pub" "$TARGET:car-courier-bootstrap/"
STEP='installing receiver with Tiny sudo'
ssh "${ADMIN_SSH[@]}" -t "$TARGET" 'bash "$HOME/car-courier-bootstrap/install-courier.sh" "$HOME/car-courier-bootstrap/drive-console-courier.pub" "$HOME/car-dashboard"'
echo 'Pairing complete. Duet downloads automatically; Tiny installs when reconnected, without Internet.'
echo 'The first full runtime download is large. Check Car updates - status or the courier log.'
