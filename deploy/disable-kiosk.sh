#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" == 0 ]]; then echo "Run as Ubuntu desktop user, not root" >&2; exit 1; fi
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
rm -f "$HOME/.config/autostart/car-dashboard-kiosk.desktop"
sudo python3 "$ROOT/deploy/kiosk-gdm.py" disable
echo "Login and kiosk autostart revert after reboot. Close existing kiosk browser separately."
