# Home Assistant on the Tiny, Duet as touchscreen

The repository can install **Home Assistant Container 2026.10.4** alongside the
existing Ubuntu dashboard, OBD recorder and GPS journal. The official image is
pulled with normal TLS verification and pinned by its repository digest. This is
Home Assistant Container, not Home Assistant OS or Supervised; it has no Supervisor
app/add-on store. Ubuntu and the existing car services remain in place.

## Recommended: only Duet connects to Internet

Use [one-device automatic updates](ONE_DEVICE_UPDATES.md) to carry the Home Assistant
image and signed Ubuntu dependencies from Duet. Tiny can be offline for its first
HA installation and later updates. The signed courier also upgrades its managed
bootstrap automatically after the initial authorization. HA stable releases are
resolved on Duet and digest-locked per bundle, with backup/health validation on Tiny.

The manual installer below is an alternative when Tiny itself has Internet.

## Existing Tiny — manual online bootstrap

On the **x86-64 ThinkCentre/Tiny**, as its normal dashboard user:

```sh
cd ~/car-dashboard
git pull --ff-only
bash deploy/install-home-assistant.sh
```

The first install needs Internet for distribution packages and the official image
(about 2.4 GB unpacked, plus Docker storage overhead). Keep at least 6 GB free;
more space and 4+ GB RAM are recommended. It installs Docker Engine from the
Ubuntu/Debian package repository if missing and requires Engine 23 or newer. If
an older Docker package is installed, upgrade Engine using Docker's official
instructions and retry. It does not install Docker Desktop.

Run this while parked. The script requests sudo, restarts only the dashboard
backend to activate its new read-only endpoint, and leaves the OBD recorder,
GPS journal, USB networking and Tailscale untouched. It rejects ARM Duet machines,
unrelated dashboard checkouts, an occupied Home Assistant port, and unmanaged
containers/configuration instead of overwriting an existing installation.

**A Git pull alone cannot grant the old updater new administrative privileges.**
Existing root-owned Git/courier helpers are stable bootstrap copies. This one-time
command is necessary on an existing Tiny. New full installations through
`deploy/install.sh` include this step automatically; set `CAR_HOME_ASSISTANT=0`
when deliberately installing only the original dashboard.

## Update the Duet client once

On the **Duet**, not the Tiny:

```sh
cd ~/car-dashboard
git pull --ff-only
bash duet/install.sh
```

Log out and back in to its graphical desktop, or restart the Duet once. This
activates its updated launcher and proxy. The existing courier can also refresh
unmodified managed Duet helpers, but running helpers still need a display restart.

The installer on Tiny extends only recognized `drive-console-duet` display-key
lines to allow forwarding to **127.0.0.1:8123** as well as 8787. Unrelated SSH keys
and shell permissions are preserved. If pairing the Duet later, the updated
`deploy/authorize-duet-key.sh` grants that same restricted pair of destinations.
A newly allowed destination takes effect when the SSH session reconnects.

Duet startup now uses a small local selection page:

- If HA is installed and enabled, it waits for the real HA frontend and opens it.
- If HA is not configured or disabled, it opens the original dashboard.
- If the Tiny/HA is unavailable, it keeps retrying and offers recovery links.

HA uses a **separate direct SSH tunnel**, Duet port 8878 → Tiny loopback 8123.
This supports Home Assistant's native WebSocket/authentication protocol without
passing it through the old read-only HTTP proxy. The legacy dashboard, archive,
CSV exports and recovery remain on Duet port 8877. If 8878 is occupied by another
application, the old display tunnel remains usable; stop that unrelated listener
before using HA. No router port forwarding or public HTTP exposure is required.

## Finish the owner account once

The installer starts Home Assistant automatically but does not create an owner,
invent a password, write authentication storage, or disable login.

On the Duet, open its startup page or visit `http://127.0.0.1:8878/` after the tunnel
connects. On a browser running on Tiny itself, use `http://127.0.0.1:8123/`. Complete
Home Assistant's normal first-run owner-account screen. Choose your own credentials
there, not in chat. Reuse the regular dedicated Duet browser profile to retain its
login between trips; don't grant trusted-network authentication to everyone.

Complete the remaining native onboarding choices for location/units and analytics
as you prefer. There is no cloud account requirement. The local car sensors keep
working when Wi-Fi/Tailscale is unavailable. Some optional Home Assistant services,
external map tiles and updates naturally require Internet; missing Internet alerts
are not evidence that local telemetry stopped.

## Dashboard editing and preconfigured car entities

The installer adds the local **Car Console** custom integration automatically.
It polls the dashboard API every five seconds; it never opens the serial adapter
or sends ECU commands. It exposes a single Car device with:

- speed, RPM, coolant temperature, ECU voltage, raw/unverified fuel percentage,
  engine load, throttle and intake temperature;
- OBD-connection and GPS-fix binary sensors;
- a real GPS device tracker, available only when a valid fresh fix exists.

Values are normalized to declared units. Missing, invalid, unknown-unit and stale
measurements become **unavailable**, not zero or current-looking old readings.
GPS with an old timestamp is unavailable. Unsupported car sensors remain unavailable
until the recorder actually supplies them.

**Overview** uses Home Assistant's native storage dashboard. Open its edit menu
and choose **Take control** when offered to use the native visual card editor.
An additional **Car starter** dashboard is preconfigured with gauges, connection
status and the remaining entities. That starter is a managed, read-only YAML
example; Overview and dashboards you create through the UI remain editable.

The original HTML/CSS/JS builder remains a separate tool; its custom projects are
not automatically converted into Home Assistant cards. The original dashboard and
all detailed reports/GPS history remain available through the recovery links.

Home Assistant's own recorder retains ten days by default. This does **not** prune
or replace the original SQLite OBD/GPS archives. HA polls are for the display;
**Reports & GPS history** still supplies the complete high-resolution archive.
HA's built-in map may need external/cached tiles; the existing archive's coordinate
plot/offline tiles remain independent.

This setup configures the **car** instance only. It does not silently pair a home
HA instance, install HACS, expose MQTT, or copy home-server credentials. Connecting
two instances over Tailscale/MQTT is a separate configuration step requiring the
home instance's address and authentication.

## Automatic configuration after Git updates (standalone installer)

The one-time bootstrap installs:

- `car-home-assistant.service`, starting the pinned container at boot;
- `car-home-assistant-reconcile.timer`, checking managed assets every two minutes;
- a root-owned `car-ha-reconcile` helper and private installation settings.

After a GitHub or courier checkout update, the timer copies changed, **unmodified
managed** integration/starter files, validates HA configuration using the actual
pinned image, and restarts an already-running HA instance only when needed. It
uses the same Git update lock to avoid reading a checkout mid-update. Failed
validation restores managed assets; a rejected config does not stop a working HA
instance. A failed restart restores the previous managed assets and restarts again.

User-edited configuration is preserved and listed in the installation status.
Native `.storage` dashboards, owner accounts, tokens and history are never rewritten
by asset reconciliation. If you customize `configuration.yaml`, keep its
`car_console:` entry to retain automatic car entities. A deliberately stopped HA
service is not resurrected by reconciliation. In this standalone/manual mode the helper/image are stable bootstrap components.
The full signed courier is the automated alternative: it updates its managed
bootstrap and delivers newer official HA stable images as well as car assets,
with offline verification and backup/rollback.

Legacy `--code-only` courier packages contain Git source only. The default full
courier now also carries the HA image and signed Ubuntu prerequisites; see the
one-device guide. It does not carry an HA instance backup. Once bootstrapped, the pinned image is cached
on Tiny and normal car-asset updates work without a registry download. Initial offline HA installation is supported by the full courier after its one-time
pairing; the standalone manual installer still needs either a carried image and
packages or Internet on Tiny.

## Status, recovery and data paths

On Tiny:

```sh
sudo systemctl status car-home-assistant --no-pager
sudo journalctl -u car-home-assistant -n 50 --no-pager
sudo journalctl -u car-home-assistant-reconcile -n 50 --no-pager
sudo /usr/local/sbin/car-ha-reconcile
curl -fsS http://127.0.0.1:8787/api/home-assistant
```

Container configuration/history: `/var/lib/car-home-assistant/config`.
Private installation settings: `/etc/car-dashboard/home-assistant-settings.json`.
Public, non-secret readiness status: `/etc/car-dashboard/home-assistant.json`.
Back up the entire HA configuration directory, including its private storage, using
HA's supported backup facilities or while its service is stopped. Protect the
backup as credentials and location history.

The installer does not mount the Docker socket, OBD devices, host root filesystem
or Bluetooth/D-Bus into HA, and does not use privileged-container mode. Its config
binds HA HTTP to Tiny loopback. The only normal host API it uses is the existing
read-only car endpoint.

To switch back permanently, on Tiny:

```sh
sudo /usr/bin/python3 -I /usr/local/lib/car-dashboard/home_assistant.py disable
```

The next Duet startup uses the original dashboard. For immediate recovery, open
`http://127.0.0.1:8877/cockpit.html` on Duet, or port 8787 on Tiny. Accounts, HA
configuration and vehicle history are retained. Rerun the install command to enable
HA again. Merely choosing the recovery link does not disable or delete HA.

Automated validation used the actual official 2026.10.4 image, its configuration
checker, authenticated REST/WebSocket APIs, synthetic OBD data and a simulated GPS
feed. Real Tiny systemd/apt operations, the physical Duet tunnel and real GPS/OBD
hardware still need checking on your machines.
