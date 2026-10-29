#!/usr/bin/env bash
# Run once on Duet with Tiny attached. Only Duet ever needs Internet.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the normal Duet user, not root.' >&2; exit 1; }
TARGET="${1:-cayden@172.16.42.2}"
[[ "$TARGET" =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+$ ]] || { echo 'Expected Tiny user@host' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
# This is your normal administrative SSH login, not the display-only key.
# OpenSSH must verify the Tiny host key; never accept a changed fingerprint blindly.
ssh "$TARGET" 'mkdir -p "$HOME/car-courier-bootstrap"'
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
scp "$ROOT/deploy/install-courier.sh" "$ROOT/deploy/offline_updates.py" \
 "$ROOT/deploy/runtime_assets.py" "$ROOT/deploy/offline_payload.py" "$ROOT/deploy/home_assistant.py" \
 "$HOME/.ssh/drive-console-courier.pub" "$TARGET:car-courier-bootstrap/"
ssh -t "$TARGET" 'bash "$HOME/car-courier-bootstrap/install-courier.sh" "$HOME/car-courier-bootstrap/drive-console-courier.pub" "$HOME/car-dashboard"'
echo 'Pairing complete. Duet downloads automatically; Tiny installs when reconnected, without Internet.'
echo 'The first full runtime download is large. Check Car updates - status or the courier log.'
