#!/usr/bin/env bash
# Duet-side opt-in automatic carry-home/carry-back updater, independent of systemd.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the Duet desktop user, not root.' >&2; exit 1; }
TARGET="${1:-cayden@172.16.42.2}"
[[ "$TARGET" =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+$ ]] || { echo 'Expected Tiny user@host' >&2; exit 1; }
case "$HOME" in *' '*|*'%'*|*"'"*) echo 'Home path contains unsupported characters.' >&2; exit 1;; esac
for c in python3 git ssh ssh-keygen; do command -v "$c" >/dev/null || { echo "Install $c first." >&2; exit 1; }; done
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
BASE="$HOME/.local/share/drive-console"
mkdir -p "$BASE" "$HOME/.local/state/drive-console" "$HOME/.config/autostart" "$HOME/.local/share/applications" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
KEY="$HOME/.ssh/drive-console-courier"
if [[ ! -f "$KEY" ]]; then ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C car-duet-update-courier; fi
chmod 600 "$KEY"
install -m 0644 "$ROOT/deploy/offline_updates.py" "$BASE/offline_updates.py"
cat > "$BASE/courier-watch.sh" <<WORKER
#!/bin/sh
exec /usr/bin/python3 -u '$BASE/offline_updates.py' watch --target '$TARGET' >> '$HOME/.local/state/drive-console/courier.log' 2>&1
WORKER
chmod 0755 "$BASE/courier-watch.sh"
cat > "$HOME/.config/autostart/car-update-courier.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Car update courier
Comment=Download at home; deliver signed updates when Tiny reconnects
Exec=$BASE/courier-watch.sh
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP
for action in prepare deliver status; do
 case "$action" in prepare) LABEL='download';; deliver) LABEL='install on Tiny';; status) LABEL='status';; esac
 cat > "$BASE/courier-$action.sh" <<COMMAND
#!/bin/sh
/usr/bin/python3 '$BASE/offline_updates.py' '$action' --target '$TARGET'
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
echo "Courier installed for $TARGET. It starts at the next graphical login."
echo "Start now: $BASE/courier-watch.sh"
echo 'First pair its separate public key with the Tiny using deploy/install-courier.sh:'
echo "$KEY.pub"
echo 'Keep the private key on the Duet. Never disable host-key verification.'
