# Home-to-car settings over Tailscale

The home panel now delivers full validated configuration snapshots through an
SSH tunnel over Tailscale. The car polls every 15 seconds and its browser checks
for changes every 45 seconds. The panel's pending list clears only after the car
has saved and read back the configuration. Disconnections retain the car's last
configuration; a lost acknowledgment is retried after reconnect or restart.

This path supports appearance, widget order/visibility, labels, units, navigation
visibility, telemetry polling, and [custom HTML/CSS/JavaScript UI projects](UI_BUILDER.md).
Custom projects run only in an isolated browser frame; this path does not install native code packages. Keep the
existing GitHub updater for software updates (`deploy/enable-updates.sh`); this
feature does not trigger it remotely. LoRaWAN, MQTT, cryptography packages and
`CAR_LINK_KEY` are not needed. Use only one configuration delivery agent per car.

## Home Linux server

Install Python 3, Git, OpenSSH server and Tailscale using your distribution and
Tailscale's official installation instructions. Join the home server and car to
the same tailnet and permit the car to SSH to the home server in the tailnet ACL.
No public port forwarding is needed. This guide uses ordinary OpenSSH over the
Tailscale network; it does not require the Tailscale SSH feature.

Clone the repository as your ordinary user at `~/car-dashboard` (or update an
existing clean checkout with `git pull --ff-only`). Then:

```sh
mkdir -p ~/.config/car-home ~/.config/systemd/user
chmod 700 ~/.config/car-home
python3 - <<'PY'
import os, secrets
from pathlib import Path
root = Path.home()/'.config/car-home'
for name in ('panel-token', 'device-token'):
    path = root/name
    with path.open('x') as f:
        os.chmod(path, 0o600)
        f.write(secrets.token_urlsafe(48)+'\n')
PY
```

Keep existing tokens if already configured; the exclusive creation above refuses
to overwrite them. These are separate secrets: the browser uses `panel-token`,
the car uses only `device-token`. Never paste either into chat or commit them.

Create `~/.config/car-home/run-panel.sh`:

```sh
#!/bin/sh
set -eu
export CAR_PANEL_TOKEN="$(cat "$HOME/.config/car-home/panel-token")"
export CAR_DEVICE_TOKEN="$(cat "$HOME/.config/car-home/device-token")"
export CAR_DEVICE_NAME=car-1
cd "$HOME/car-dashboard"
exec /usr/bin/python3 -m control.home_server
```

Create `~/.config/systemd/user/car-home-panel.service`:

```ini
[Unit]
Description=Home head-unit control panel
After=network.target
[Service]
ExecStart=/bin/sh %h/.config/car-home/run-panel.sh
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now car-home-panel
sudo loginctl enable-linger "$USER"
```

The panel binds only to home-server loopback port 8790. From your laptop use
`ssh -N -L 8790:127.0.0.1:8790 HOME_USER@HOME_TAILSCALE_NAME`, then open
`http://127.0.0.1:8790` in the laptop browser. Enter the panel token from your
local secure file and select device `car-1`. Do not run the separate experimental
`home_server.server` service on the same port.

## Car Linux computer

Update the checkout at `~/car-dashboard`. Set up a dedicated SSH key for the
car's ordinary dashboard user and verify the home host key through a trusted
channel. Test a noninteractive connection with `BatchMode=yes`. Restrict the
car key's home `authorized_keys` entry with:

```text
restrict,port-forwarding,permitopen="127.0.0.1:8790",command="/bin/false" ssh-ed25519 YOUR_CAR_PUBLIC_KEY
```

Use `ssh -N` for forwarding (it does not request the forced command). Copy the
home device-token securely using your administrative access, such as SCP, to
`~/.config/car-dashboard/device-token` on the car; the restricted tunnel key
cannot run SCP. Make its directory mode 700 and file mode 600. Do not copy the
panel token to the car.

```sh
mkdir -p ~/.config/systemd/user ~/.config/car-dashboard
chmod 700 ~/.config/car-dashboard
chmod 600 ~/.config/car-dashboard/device-token
```

Create `~/.config/systemd/user/car-home-tunnel.service`, replacing
`HOME_USER@HOME_TAILSCALE_NAME` with your home server's login and tailnet name:

```ini
[Unit]
Description=Car to home control SSH tunnel over Tailscale
After=network-online.target
[Service]
ExecStart=/usr/bin/ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:8791:127.0.0.1:8790 HOME_USER@HOME_TAILSCALE_NAME
Restart=always
RestartSec=10
[Install]
WantedBy=default.target
```

Create `~/.config/systemd/user/car-home-sync.service`:

```ini
[Unit]
Description=Save and acknowledge home head-unit settings
Wants=car-home-tunnel.service
After=car-home-tunnel.service
[Service]
WorkingDirectory=%h/car-dashboard
Environment=CAR_HOME_URL=http://127.0.0.1:8791
Environment=CAR_DEVICE_TOKEN_FILE=%h/.config/car-dashboard/device-token
Environment=CAR_UI_CONFIG=%h/.config/car-dashboard/ui-config.json
ExecStart=/usr/bin/python3 -m control.tailscale_agent
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true
[Install]
WantedBy=default.target
```

Make the dashboard read that exact same config file: add
`CAR_UI_CONFIG=/home/YOUR_CAR_USER/.config/car-dashboard/ui-config.json` to
`/etc/car-dashboard/dashboard.env`, replacing the username. If an existing
configuration exists at another path, preserve and migrate it first. If its
revision exceeds the home revision, sync stops rather than rolling it back;
reconcile the two configurations before enabling sync. Do not delete a live
configuration to bypass a revision conflict.

```sh
sudo systemctl restart car-dashboard
systemctl --user daemon-reload
systemctl --user enable --now car-home-tunnel car-home-sync
sudo loginctl enable-linger "$USER"
```

## Verify and troubleshoot

Save a day/night or widget change in the home panel. Within about one minute,
the car should display it and the panel's configuration queue should empty.
On the car:

```sh
journalctl --user -u car-home-sync -u car-home-tunnel -n 50 --no-pager
curl -fsS http://127.0.0.1:8787/api/ui-config
```

On the home server inspect `journalctl --user -u car-home-panel`. For SSH errors,
check tailnet ACLs, the login user, key permissions, and known_hosts. A failed
sync never clears the queue. Same-revision differences and a home revision
behind the car are rejected. A restored home backup must be reconciled before
delivery resumes. The device token is scoped to `CAR_DEVICE_NAME` and cannot
edit settings or use admin endpoints. Rotate it on both machines if compromised.

Cloud tests exercise the HTTP delivery, authorization, persistence failures and
retry behavior. Actual Tailscale connectivity and systemd deployment still need
verification on your two Linux machines.
