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
import secrets,sys,os,shlex
path=Path(sys.argv[1])
def credential(name,legacy,minimum):
    previous=Path(legacy)
    value=os.getenv(name) or (previous.read_text().strip() if previous.exists() else secrets.token_hex(32))
    if len(value)<minimum or '\n' in value or '\0' in value:raise SystemExit('Invalid existing '+name+'; configuration unchanged')
    return value
values={
 'CAR_PANEL_TOKEN':credential('CAR_PANEL_TOKEN',path.parent/'panel-token',24),
 'CAR_DEVICE_TOKEN':credential('CAR_DEVICE_TOKEN',path.parent/'device-token',32),
 'CAR_LINK_KEY':credential('CAR_LINK_KEY',Path.home()/'.local/share/car-home-control/software/update-link.key',64),
 'CAR_PANEL_BIND':'127.0.0.1','CAR_PANEL_PORT':'8790',
}
if values['CAR_PANEL_TOKEN']==values['CAR_DEVICE_TOKEN']:raise SystemExit('Panel and device tokens must differ; configuration unchanged')
path.write_text(''.join(name+'='+shlex.quote(value)+'\n' for name,value in values.items()))
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
for entry in 'car-home-control control.home_server' 'car-home-radio control.chirpstack_bridge' 'car-home-ha control.home_ha' 'car-home-sync control.file_sync'; do
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
# The previous documented deployment used this exact user unit on port 8790.
# Retire only that managed predecessor; keep its files, state and credentials.
(cd "$ROOT" && python3 -m home_server.migrate_control)
sudo systemctl daemon-reload
sudo systemctl enable --now car-home-control.service car-home-radio.service car-home-ha.service car-home-sync.service
sudo systemctl restart car-home-control.service car-home-radio.service car-home-ha.service car-home-sync.service
(cd "$ROOT" && python3 - "$ENVFILE" <<'PYHEALTH'
from pathlib import Path
import sys,time
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError,URLError
from control.service_watch import environment
env=environment(Path(sys.argv[1]))
host=env.get('CAR_PANEL_BIND','127.0.0.1')
if host=='0.0.0.0':host='127.0.0.1'
url='http://'+host+':'+env.get('CAR_PANEL_PORT','8790')+'/api/device/software/credentials'
request=Request(url,headers={'Authorization':'Bearer '+env.get('CAR_DEVICE_TOKEN','')})
client=build_opener(ProxyHandler({}))
for attempt in range(15):
    try:
        with client.open(request,timeout=2) as response:
            if response.status==200:
                print('Home control credential check succeeded.')
                break
    except HTTPError as error:
        if error.code==401:raise SystemExit('Home listener rejected its saved device token. Check for another service owning the panel port; credentials were not changed.')
    except (URLError,TimeoutError,OSError):pass
    time.sleep(.5)
else:raise SystemExit('Home control is not ready. Inspect car-home-control.service logs; installation is not yet verified.')
PYHEALTH
)
echo 'Home panel, radio bridge, selected-file sync and home Home Assistant MQTT services are installed.'
echo 'Home HA discovery waits for CAR_HA_MQTT_HOST and your existing broker credentials in control.env.'
echo 'Radio waits for its real ChirpStack application/DevEUI configuration; no guessed modem or credentials are used.'
echo 'Existing authentication credentials in ~/.config/car-home/control.env were preserved.'
