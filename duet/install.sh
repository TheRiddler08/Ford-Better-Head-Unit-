#!/usr/bin/env bash
# Install the display client on a Linux Lenovo Duet 3. No ChromeOS flashing.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo "Run as the Duet's desktop login user, not root." >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
for bin in python3 ssh ssh-keygen; do
  command -v "$bin" >/dev/null || { echo "Missing $bin; install python3 and openssh-client first." >&2; exit 1; }
done
if command -v firefox >/dev/null 2>&1; then
  BROWSER=firefox
elif command -v chromium >/dev/null 2>&1; then
  BROWSER=chromium
else
  echo "Install Firefox or Chromium on the Duet before enabling the touchscreen kiosk." >&2
  exit 1
fi
mkdir -p "$HOME/.local/share/drive-console" "$HOME/.config/autostart" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
install -m 0644 "$ROOT/proxy.py" "$HOME/.local/share/drive-console/proxy.py"
install -m 0644 "$ROOT/launcher_lock.py" "$HOME/.local/share/drive-console/launcher_lock.py"
install -m 0755 "$ROOT/launch.sh" "$HOME/.local/share/drive-console/launch.sh"
if [[ ! -f "$HOME/.ssh/drive-console-duet" ]]; then
  ssh-keygen -q -t ed25519 -a 64 -N '' -f "$HOME/.ssh/drive-console-duet" -C "drive-console-duet-display"
fi
chmod 600 "$HOME/.ssh/drive-console-duet"
# Literal local path: reject whitespace to avoid broken XDG Exec syntax.
case "$HOME" in *" "*|*"%"*) echo "Home directory with spaces or percent not supported." >&2; exit 1;; esac
cat > "$HOME/.config/autostart/drive-console-duet.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Drive Console USB Display
Comment=Launch reconnecting touchscreen over the Duet's USB network connection
Exec=/usr/bin/python3 $HOME/.local/share/drive-console/launcher_lock.py $HOME/.local/share/drive-console/launch.sh
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
chmod 644 "$HOME/.config/autostart/drive-console-duet.desktop"
echo
echo "Duet display installed. Browser: $BROWSER"
echo "Duet public SSH key — copy this to the Tiny using deploy/authorize-duet-key.sh:"
cat "$HOME/.ssh/drive-console-duet.pub"
echo
echo "IMPORTANT: verify and pin the Tiny's SSH host key in ~/.ssh/known_hosts."
echo "On Tiny: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
echo "On Duet: ssh-keyscan -t ed25519 172.16.42.2 | ssh-keygen -lf -"
echo "After confirming fingerprints match, save: ssh-keyscan -H -t ed25519 172.16.42.2 >> ~/.ssh/known_hosts"
echo "Kiosk starts automatically at next graphical login; no reboot was triggered."
echo "Manual launch (from Duet's graphical terminal): python3 ~/.local/share/drive-console/launcher_lock.py ~/.local/share/drive-console/launch.sh"
