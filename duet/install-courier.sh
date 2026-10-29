#!/usr/bin/env bash
# One-time Duet worker install. No Docker, user D-Bus or Tiny Internet required.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the Duet desktop user, not root.' >&2; exit 1; }
TARGET="${1:-cayden@172.16.42.2}"
[[ "$TARGET" =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+$ ]] || { echo 'Expected Tiny user@host' >&2; exit 1; }
[[ "$HOME" =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo 'Home path contains unsupported characters.' >&2; exit 1; }
for c in python3 git ssh ssh-keygen; do command -v "$c" >/dev/null || { echo "Install $c first." >&2; exit 1; }; done
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
BASE="$HOME/.local/share/drive-console"
mkdir -p "$BASE" "$HOME/.local/state/drive-console" "$HOME/.config/autostart" "$HOME/.local/share/applications" "$HOME/.ssh"
chmod 700 "$HOME/.ssh" "$HOME/.local/state/drive-console"
if ! python3 -c 'import ctypes; ctypes.CDLL("libzstd.so.1")' 2>/dev/null; then
 if command -v apt-get >/dev/null; then sudo apt-get update; sudo apt-get install -y libzstd1
 elif command -v apk >/dev/null; then sudo apk add zstd-libs
 else echo 'Install the system zstd library on the Duet before continuing.' >&2; exit 1; fi
fi
KEY="$HOME/.ssh/drive-console-courier"
if [[ ! -f "$KEY" ]]; then ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C car-duet-update-courier; fi
chmod 600 "$KEY"
BOOTSTRAP_ID="$(python3 - "$ROOT/deploy" <<'PYID'
import hashlib,sys
from pathlib import Path
h=hashlib.sha256()
for name in ('offline_updates.py','runtime_assets.py','offline_payload.py','home_assistant.py'):h.update((Path(sys.argv[1])/name).read_bytes())
print('bootstrap-'+h.hexdigest())
PYID
)"
mkdir -p "$BASE/courier-versions/$BOOTSTRAP_ID"
for name in offline_updates.py runtime_assets.py offline_payload.py home_assistant.py; do
 install -m 0644 "$ROOT/deploy/$name" "$BASE/courier-versions/$BOOTSTRAP_ID/$name"
done
ln -sfn "courier-versions/$BOOTSTRAP_ID" "$BASE/courier-next"
mv -Tf "$BASE/courier-next" "$BASE/courier-current"
# A one-time migration can stop the old worker; routine future self-upgrades
# wait until active transfers finish. Never target the browser or display tunnel.
python3 - "$BASE" <<'PYSTOP'
import os,signal,sys,time
from pathlib import Path
base=Path(sys.argv[1])
for entry in Path('/proc').iterdir():
 if not entry.name.isdigit() or int(entry.name)==os.getpid():continue
 try:
  if entry.stat().st_uid!=os.getuid():continue
  argv=(entry/'cmdline').read_bytes().split(b'\0')
  wrappers=[Path(arg.decode()) for arg in argv if arg.endswith(b'/courier-watch.sh')]
  if wrappers and any(path.is_relative_to(base) for path in wrappers):
   os.kill(int(entry.name),signal.SIGTERM);continue
  if b'watch' not in argv:continue
  scripts=[Path(arg.decode()) for arg in argv if arg.endswith(b'/offline_updates.py')]
  if not scripts or not any(path.is_relative_to(base) for path in scripts):continue
  os.kill(int(entry.name),signal.SIGTERM)
 except (OSError,UnicodeError):pass
PYSTOP
cat > "$BASE/courier-watch.sh" <<WORKER
#!/bin/sh
while :; do
 /usr/bin/python3 -u '$BASE/courier-current/offline_updates.py' watch --target '$TARGET' >> '$HOME/.local/state/drive-console/courier.log' 2>&1
 sleep 10
done
WORKER
chmod 0755 "$BASE/courier-watch.sh"
for action in prepare deliver status; do
 case "$action" in prepare) LABEL='download';; deliver) LABEL='install on Tiny';; status) LABEL='status';; esac
 cat > "$BASE/courier-$action.sh" <<COMMAND
#!/bin/sh
/usr/bin/python3 '$BASE/courier-current/offline_updates.py' '$action' --target '$TARGET'
result=\$?
printf '\nPress Enter to close...'
read answer || true
exit "\$result"
COMMAND
 chmod 0755 "$BASE/courier-$action.sh"
 cat > "$HOME/.local/share/applications/car-courier-$action.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Car updates - $LABEL
Exec=$BASE/courier-$action.sh
Terminal=true
Categories=Utility;
DESKTOP
done
USER_NAME="$(id -un)"
if [[ -d /run/systemd/system ]]; then
 sudo tee /etc/systemd/system/car-duet-courier.service >/dev/null <<UNIT
[Unit]
Description=Download and carry offline car updates
Wants=network-online.target
After=network-online.target
[Service]
User=$USER_NAME
Environment=HOME=$HOME
ExecStart=/usr/bin/python3 -u $BASE/courier-current/offline_updates.py watch --target $TARGET
Restart=always
RestartSec=10
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
 sudo systemctl daemon-reload
 sudo systemctl enable --now car-duet-courier.service
 rm -f "$HOME/.config/autostart/car-update-courier.desktop"
 echo 'Courier enabled at boot through systemd; no graphical login or user bus required.'
elif [[ -d /run/openrc ]] && command -v rc-update >/dev/null; then
 sudo tee /etc/init.d/car-duet-courier >/dev/null <<SERVICE
#!/sbin/openrc-run
name="Car update courier"
supervisor="supervise-daemon"
command="/usr/bin/python3"
command_args="-u $BASE/courier-current/offline_updates.py watch --target $TARGET"
command_user="$USER_NAME:$(id -gn)"
export HOME="$HOME"
directory="$HOME"
respawn_delay=10
respawn_max=0
output_log="$HOME/.local/state/drive-console/courier.log"
error_log="$HOME/.local/state/drive-console/courier.log"
depend() { use net; after bootmisc; }
SERVICE
 sudo chmod 0755 /etc/init.d/car-duet-courier
 sudo rc-update add car-duet-courier default
 sudo rc-service car-duet-courier restart
 rm -f "$HOME/.config/autostart/car-update-courier.desktop"
 echo 'Courier enabled at boot through OpenRC.'
else
 cat > "$HOME/.config/autostart/car-update-courier.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Car update courier
Exec=$BASE/courier-watch.sh
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP
 nohup "$BASE/courier-watch.sh" >/dev/null 2>&1 &
 echo 'Courier started now; automatic restart uses graphical login on this OS.'
fi
echo "Downloads run automatically; delivery retries when $TARGET reconnects."
echo 'First pair the Tiny once with duet/setup-offline-updates.sh if not already paired.'
