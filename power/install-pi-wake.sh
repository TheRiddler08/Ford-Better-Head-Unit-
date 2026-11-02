#!/usr/bin/env bash
# Raspberry Pi OS/systemd. Supply real adapter MACs and the actual LAN broadcast.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as your normal Pi user.' >&2; exit 1; }
CONFIG="${1:?Usage: bash power/install-pi-wake.sh /path/to/your-pi-wake.json}"
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
python3 "$ROOT/power/wake.py" --check --config "$CONFIG"
# Explicit example addresses must not silently become the installed setup.
python3 - "$CONFIG" <<'PY'
import json,sys
if any(row['mac'].lower().startswith('02:00:00:00:00:0') for row in json.load(open(sys.argv[1]))['targets']):
 raise SystemExit('Replace the example MAC addresses with the real target Ethernet MACs.')
PY
sudo install -d -m 0755 /etc/car-dashboard /usr/local/lib/car-dashboard
sudo install -o root -g root -m 0644 "$CONFIG" /etc/car-dashboard/pi-wake.json
sudo install -o root -g root -m 0644 "$ROOT/power/wake.py" /usr/local/lib/car-dashboard/pi-wake.py
sudo tee /etc/systemd/system/car-wake@.service >/dev/null <<'UNIT'
[Unit]
Description=Wake car Tiny and Duet (%i attempt)
After=network.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /usr/local/lib/car-dashboard/pi-wake.py
DynamicUser=yes
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
RestrictAddressFamilies=AF_INET AF_UNIX
TimeoutStartSec=30
UNIT
sudo tee /etc/systemd/system/car-wake-status.service >/dev/null <<'UNIT'
[Unit]
Description=Pi wake-window timing on the central Ethernet LAN
After=network.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 -I /usr/local/lib/car-dashboard/pi-wake.py --serve
DynamicUser=yes
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
RestrictAddressFamilies=AF_INET AF_UNIX
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
for attempt in first retry; do
 if [[ "$attempt" == first ]]; then SECONDS_AFTER_BOOT=30; else SECONDS_AFTER_BOOT=150; fi
 sudo tee "/etc/systemd/system/car-wake-$attempt.timer" >/dev/null <<UNIT
[Unit]
Description=Car wake $attempt at $SECONDS_AFTER_BOOT seconds after Pi Linux boot
[Timer]
OnBootSec=$SECONDS_AFTER_BOOT
AccuracySec=1
Unit=car-wake@$attempt.service
[Install]
WantedBy=timers.target
UNIT
done
sudo systemctl daemon-reload
sudo systemctl enable car-wake-first.timer car-wake-retry.timer
sudo systemctl enable car-wake-status.service
# Start on next boot. Installing later in a boot must not wake devices immediately.
echo 'Enabled for next Pi boot: WOL at 30 seconds, then at 150 seconds.'
echo 'Test now if desired: sudo systemctl start car-wake@manual.service'
