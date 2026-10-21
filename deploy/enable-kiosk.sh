#!/usr/bin/env bash
# Enable full-screen dashboard after GDM automatically logs in.
set -euo pipefail
if [[ "$EUID" == 0 ]]; then echo "Run as the ordinary Ubuntu desktop user, not root" >&2; exit 1; fi
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
LOGIN_USER="$(id -un)"
[[ "$ROOT" != *' '* && "$ROOT" != *'%'* ]] || { echo "No spaces or % in install path" >&2; exit 1; }
[[ -f /etc/gdm3/custom.conf ]] || { echo "GDM3 not installed at /etc/gdm3/custom.conf" >&2; exit 1; }
[[ -f /etc/systemd/system/car-dashboard.service ]] || { echo "Install dashboard first: bash deploy/install.sh" >&2; exit 1; }
BROWSER=""
for exe in chromium chromium-browser google-chrome-stable google-chrome firefox; do
  if command -v "$exe" >/dev/null 2>&1; then BROWSER="$exe"; break; fi
done
if [[ -z "$BROWSER" ]]; then
  echo "Installing Firefox for the local kiosk..."
  sudo apt-get update
  sudo apt-get install -y firefox
  command -v firefox >/dev/null || { echo "Browser installation failed" >&2; exit 1; }
fi
command -v curl >/dev/null || { echo "Please install curl first: sudo apt install curl" >&2; exit 1; }
mkdir -p "$HOME/.config/autostart"
TMP="$(mktemp "$HOME/.config/autostart/.car-dashboard.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<EOF
[Desktop Entry]
Type=Application
Name=Car Dashboard Kiosk
Comment=Full-screen offline-first car dashboard
Exec=/bin/bash $ROOT/deploy/kiosk-launch.sh
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
EOF
chmod 0644 "$TMP"
mv "$TMP" "$HOME/.config/autostart/car-dashboard-kiosk.desktop"
sudo python3 "$ROOT/deploy/kiosk-gdm.py" enable "$LOGIN_USER"
echo "Kiosk enabled for $LOGIN_USER on next reboot; current display session untouched."
echo "Physical access can expose this user's files and privileges. See README."
echo "Disable with: bash deploy/disable-kiosk.sh ; then reboot."
