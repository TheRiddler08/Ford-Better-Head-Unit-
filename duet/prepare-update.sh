#!/usr/bin/env bash
# Start on Wi-Fi with Tiny absent. Finish local pairing when it returns.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as your normal Duet user, not root.' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
MODE=prepare
if [[ "${1:-}" == --resume ]]; then MODE=resume; shift; fi
TARGET="${1:-cayden@172.16.42.2}"
[[ "$TARGET" =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+$ ]] || { echo 'Expected Tiny user@host' >&2; exit 1; }
[[ "$HOME" =~ ^/[a-zA-Z0-9_./-]+$ && "$ROOT" =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'Unsupported home/repository path.' >&2; exit 1; }
BASE="$HOME/.local/share/drive-console"
AUTOSTART="$HOME/.config/autostart/car-courier-pairing.desktop"
if [[ "$MODE" == prepare ]]; then
    bash "$ROOT/duet/install.sh"
    bash "$ROOT/duet/install-courier.sh" "$TARGET"
    # Persist only a local interactive pairing reminder, never a password.
    # Downloads use the separate boot worker and continue without this terminal.
    printf '#!/usr/bin/env bash\nexec bash %q --resume %q\n' "$ROOT/duet/prepare-update.sh" "$TARGET" > "$BASE/finish-courier-pairing.sh"
    chmod 0755 "$BASE/finish-courier-pairing.sh"
    cat > "$AUTOSTART" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Finish Tiny offline update pairing
Exec=$BASE/finish-courier-pairing.sh
Terminal=true
X-GNOME-Autostart-enabled=true
DESKTOP
fi
echo 'Duet downloads are running. Tiny does not need Internet.'
echo "Waiting for $TARGET on the local connection. Reconnect Tiny when ready."
echo 'Keep this terminal open. If Duet reboots, pairing resumes at the next desktop login.'
echo 'An old/unpaired Tiny may ask for your normal SSH/sudo password once.'
python3 - "$ROOT" "$TARGET" "$BASE" "$AUTOSTART" <<'PY'
import fcntl
from pathlib import Path
import socket
import subprocess
import sys
import time

root,target,base,autostart=sys.argv[1:]
lock=(Path(base)/'pairing.lock').open('a')
try:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Pairing is already waiting in another terminal.')
host=target.split('@',1)[1]
try:
    while True:
        try:
            with socket.create_connection((host,22),timeout=2):pass
            break
        except OSError:
            time.sleep(5)
    # Reachability is not authentication. Normal SSH still verifies the host key
    # and asks for admin authorization. The restricted courier key cannot do this.
    print('Tiny detected. Upgrading/pairing its receiver over the local link.',flush=True)
    # This Python program came from a heredoc; restore the actual terminal so
    # SSH can allocate a PTY and sudo can ask for authorization on the Tiny.
    with open('/dev/tty','r+') as terminal:
        result=subprocess.run(['bash',str(Path(root)/'duet/setup-offline-updates.sh'),target],stdin=terminal)
    if result.returncode:
        print('Pairing did not finish. Downloads remain active; rerun:',flush=True)
        print(str(Path(base)/'finish-courier-pairing.sh'),flush=True)
        raise SystemExit(result.returncode)
    Path(autostart).unlink(missing_ok=True)
    print('Receiver ready. The courier will install the completed bundle automatically.',flush=True)
    print('Keep Tiny powered throughout installation. A download must finish before it can be carried offline.',flush=True)
except KeyboardInterrupt:
    raise SystemExit('Waiting stopped. Downloads continue; run finish-courier-pairing.sh to resume.')
PY
