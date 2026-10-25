#!/usr/bin/env bash
# Make the ThinkCentre Tiny a monitor-free backend at the NEXT reboot.
# Does not stop the current GUI or network; deliberately opt-in.
set -euo pipefail

if (( EUID == 0 )); then
  echo "Run as the Tiny's normal Ubuntu user; sudo is requested when needed." >&2
  exit 1
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

if ! systemctl is-active --quiet car-dashboard.service; then
  echo "car-dashboard.service is not running; refusing headless setup." >&2
  exit 1
fi
if ! curl -fsS --max-time 5 http://127.0.0.1:8787/api/health >/dev/null; then
  echo "Dashboard health check failed; refusing headless setup." >&2
  exit 1
fi
if ! systemctl is-active --quiet ssh.service; then
  echo "OpenSSH service is not running. Install and enable openssh-server first." >&2
  exit 1
fi

# The Duet kiosk runs on a different device. Prevent an old Tiny desktop
# auto-login/kiosk from starting again while leaving the current session alone.
if [[ -f /etc/gdm3/custom.conf ]]; then
  sudo python3 "$ROOT/deploy/kiosk-gdm.py" disable
fi
rm -f "$HOME/.config/autostart/car-dashboard-kiosk.desktop"

# A persistent marker tells future dashboard reinstalls NOT to restore a
# graphical kiosk by default. No other unit is disabled by this script.
sudo install -d -m 0755 /etc/car-dashboard
printf 'Headless Tiny: Duet handles the display. To restore GUI, see docs/DUET_OFFLINE_ETHERNET.md\n' |
  sudo tee /etc/car-dashboard/headless-mode >/dev/null
sudo chmod 0644 /etc/car-dashboard/headless-mode

# Enable booting without a display manager *on the next reboot only*.
sudo systemctl set-default multi-user.target
sudo systemctl enable car-dashboard.service
sudo systemctl enable ssh.service

echo "Tiny will boot without a graphical session after the next reboot."
echo "Dashboard and SSH are kept enabled. Ethernet and Tailscale were not modified."
echo "Verify from the Duet (with the Tiny monitor detached) before putting it in the car."
echo "Rollback: sudo systemctl set-default graphical.target"
echo "          sudo rm /etc/car-dashboard/headless-mode"
echo "          bash deploy/enable-kiosk.sh   # if local kiosk is desired"
