#!/usr/bin/env bash
# Install on Tiny/Duet only. DOES NOT suspend the current SSH session.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the normal device user.' >&2; exit 1; }
ROLE="${1:?Usage: bash power/install-boot-standby.sh tiny-or-duet ethernet-interface pi-ethernet-ip [--arm] [--deep]}"
NIC="${2:?Specify the Ethernet adapter that the Pi can reach}"
[[ "$ROLE" == duet || "$ROLE" == tiny ]] || exit 2
[[ "$NIC" =~ ^[a-zA-Z0-9_.:-]{1,15}$ ]] || exit 2
PI_ADDRESS="${3:?Specify the Pi Ethernet IPv4 address}"
ARM=false
SLEEP_MODE=current
for option in "${@:4}"; do
 case "$option" in --arm) ARM=true;; --deep) SLEEP_MODE=deep;; *) echo "Unknown option: $option" >&2; exit 2;; esac
done
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
command -v ethtool >/dev/null || { echo 'Install ethtool first (Tiny full courier includes it).' >&2; exit 1; }
if [[ -d /run/systemd/system ]]; then INIT=systemd
elif [[ -d /run/openrc ]] && command -v rc-update >/dev/null && command -v loginctl >/dev/null; then INIT=openrc
else echo 'Requires systemd, or OpenRC plus elogind/loginctl. Keeping device awake.' >&2; exit 1; fi
TEMP="$(mktemp)"
trap 'rm -f "$TEMP"' EXIT
python3 - "$ROLE" "$NIC" "$HOME" "$ARM" "$SLEEP_MODE" "$PI_ADDRESS" > "$TEMP" <<'PY'
import json,sys
role,nic,home,armed,mode,pi=sys.argv[1:]
print(json.dumps(dict(schema=1,role=role,interface=nic,home=home,armed=armed=='true',sleep_mode=mode,pi_address=pi)))
PY
sudo install -d -m 0755 /etc/car-dashboard /usr/local/lib/car-dashboard
sudo install -o root -g root -m 0644 "$ROOT/power/standby.py" /usr/local/lib/car-dashboard/boot-standby.py
# Validate the proposed config before replacing an existing working setup.
sudo /usr/bin/python3 -I /usr/local/lib/car-dashboard/boot-standby.py check --config "$TEMP"
sudo install -o root -g root -m 0644 "$TEMP" /etc/car-dashboard/boot-standby.json
if [[ "$INIT" == systemd ]]; then
 sudo tee /etc/systemd/system/car-boot-standby.service >/dev/null <<'UNIT'
[Unit]
Description=Opt-in early boot suspend waiting for the Pi
After=network.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /usr/local/lib/car-dashboard/boot-standby.py boot
TimeoutStartSec=infinity
RemainAfterExit=yes
UNIT
 sudo tee /etc/systemd/system/car-boot-standby.timer >/dev/null <<'UNIT'
[Unit]
Description=Consider standby once, 15 seconds after Linux boot
[Timer]
OnBootSec=15
AccuracySec=1
Unit=car-boot-standby.service
[Install]
WantedBy=timers.target
UNIT
 sudo systemctl daemon-reload
 if [[ "$ARM" == true ]]; then sudo systemctl enable car-boot-standby.timer
 else sudo systemctl disable --now car-boot-standby.timer; fi
else
 sudo tee /etc/init.d/car-boot-standby >/dev/null <<'UNIT'
#!/sbin/openrc-run
name="Early boot standby waiting for Pi"
command="/usr/bin/python3"
command_args="-I /usr/local/lib/car-dashboard/boot-standby.py boot"
command_background=true
pidfile="/run/car-boot-standby.pid"
output_log="/var/log/car-boot-standby.log"
error_log="/var/log/car-boot-standby.log"
depend() { use net; after bootmisc; }
UNIT
 sudo chmod 0755 /etc/init.d/car-boot-standby
 if [[ "$ARM" == true ]]; then sudo rc-update add car-boot-standby default
 else sudo rc-update del car-boot-standby default || true; fi
fi
echo "Boot standby armed=$ARM. This installer does not suspend now."
echo 'Before arming, test wake from a local console: sudo python3 -I /usr/local/lib/car-dashboard/boot-standby.py test-suspend'
echo 'Firmware must separately support power-on after AC returns. See docs/PI_WAKE_POWER.md.'
