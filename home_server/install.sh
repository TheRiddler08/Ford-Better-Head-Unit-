#!/usr/bin/env bash
# Normal home install applies the panel, signing keys and optional radio bridge.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as your normal home-server user.' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
sudo apt-get update
sudo apt-get install -y python3-cryptography python3-paho-mqtt python3-serial python3-nacl
mkdir -p "$HOME/.config/car-home" "$HOME/.local/share/car-home-control"
chmod 700 "$HOME/.config/car-home" "$HOME/.local/share/car-home-control"
ENVFILE="$HOME/.config/car-home/control.env"
if [[ ! -f "$ENVFILE" ]]; then
 python3 - "$ENVFILE" <<'PY'
from pathlib import Path
import secrets,sys,os
path=Path(sys.argv[1]);path.write_text('CAR_PANEL_TOKEN='+(os.getenv('CAR_PANEL_TOKEN') or secrets.token_hex(32))+'\nCAR_DEVICE_TOKEN='+(os.getenv('CAR_DEVICE_TOKEN') or secrets.token_hex(32))+'\nCAR_LINK_KEY='+(os.getenv('CAR_LINK_KEY') or secrets.token_hex(32))+'\nCAR_PANEL_BIND=127.0.0.1\nCAR_PANEL_PORT=8790\n')
path.chmod(0o600)
PY
fi
# Existing installations keep their established link key. Seed only a missing key.
python3 - "$ENVFILE" "$HOME/.local/share/car-home-control/software/update-link.key" <<'PYKEY'
from pathlib import Path
import secrets,sys,os
path=Path(sys.argv[1]);text=path.read_text()
if not any(line.startswith('CAR_LINK_KEY=') for line in text.splitlines()):
 key=Path(sys.argv[2]);value=os.getenv('CAR_LINK_KEY') or (key.read_text().strip() if key.exists() else secrets.token_hex(32))
 with path.open('a') as file:file.write('\nCAR_LINK_KEY='+value+'\n')
# Import supplied CAR_ settings once without printing their values.
import shlex
known={line.split('=',1)[0] for line in path.read_text().splitlines() if '=' in line}
with path.open('a') as file:
 for name,value in os.environ.items():
  if name.startswith('CAR_') and name not in known and '\n' not in value and '\0' not in value:file.write(name+'='+shlex.quote(value)+'\n')
path.chmod(0o600)
PYKEY
for entry in 'car-home-control control.home_server' 'car-home-radio control.chirpstack_bridge'; do
 read -r NAME MODULE <<< "$entry"
 sudo tee "/etc/systemd/system/$NAME.service" >/dev/null <<UNIT
[Unit]
Description=Automatic car home service ($MODULE)
After=network-online.target
[Service]
User=$(id -un)
Environment=HOME=$HOME
WorkingDirectory=$ROOT
ExecStart=/usr/bin/python3 -m control.service_watch --env $ENVFILE --module $MODULE
Restart=always
RestartSec=15
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
done
sudo systemctl daemon-reload
sudo systemctl enable --now car-home-control.service car-home-radio.service
sudo systemctl restart car-home-control.service car-home-radio.service
echo 'Home panel, signing keys and radio bridge services are installed.'
echo 'Radio waits for its real ChirpStack application/DevEUI configuration; no guessed modem or credentials are used.'
echo 'Existing authentication credentials in ~/.config/car-home/control.env were preserved.'
