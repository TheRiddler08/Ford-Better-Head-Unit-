# Car Assistant on the Tiny, Duet as touchscreen

The repository installs **Car Assistant**, our source fork of Home Assistant Core
2026.10.4 and its matching 20260930.5 frontend. The copied source, licenses, revision
pins and modifications are in [car_assistant](../car_assistant/README.md). It keeps
the native dashboard editor/cards and existing accounts/layouts. Ubuntu, the
original dashboard, OBD recorder and GPS journal remain in place.

The pinned official Container image supplies Python/native dependencies. A verified
source/frontend archive overlays it in a local derived image. It is not Home
Assistant OS or Supervised; there is no Supervisor app/add-on store.

## Offline startup

The fork removes automation/script runtime setup, their configuration APIs and
pages, cloud and voice/Assist runtime features and pages, and area/room/floor
management pages and write APIs. New accounts no longer create household rooms.
Shared read-only registries, schemas and imported libraries remain for existing
cards/devices; saved data is preserved. The unneeded Internet alerts lookup is
also removed. Other integration implementations/defaults remain available,
including Bluetooth/discovery; unsupported hardware permissions may still report
warnings. Scenes, helpers, history, maps and normal dashboard editing remain.

The runtime is applied through normal installation and signed courier updates.
Tiny builds it from the carried dependency base without Internet or Node tooling.
An older receiver first installs the carried base, then its refreshed manager
reconciles the fork on the usual timer. A failed build leaves the running image
alone; failed replacement readiness restores the previous container/configuration.
Edited `configuration.yaml` files are preserved and reported rather than replaced;
native dashboard layouts and accounts are untouched. There is no fixed startup
time guarantee: Docker, storage, integrations and the Duet tunnel affect it.
Bootstrap timing is logged at INFO level so later startup reports include the
initialization duration without enabling verbose logging for every integration.

For diagnosis, inspect `journalctl -b -u car-home-assistant` and the container
logs. `homeassistant_alerts` DNS failures indicate an unnecessary Internet check
under the previous defaults. A message that pending HTTP configuration was not
confirmed within five minutes describes Home Assistant's HTTP safety rollback
and restart, not a normal five-minute boot delay. Keep that safeguard; confirm
intentional HTTP changes through Home Assistant rather than editing auth/storage.

`tests/native_offline_startup.py` validates the packaged fork on an internal Docker
LAN with no Internet route and a fresh disposable `/config`: frontend readiness,
owner onboarding, live car entities, native dashboard saving, disabled feature
domains and read-only area registry behavior. An internal LAN keeps the multicast
interface expected by the retained discovery integrations.

## Recommended: only Duet connects to Internet

Use [one-device automatic updates](ONE_DEVICE_UPDATES.md) to carry the Home Assistant
image and signed Ubuntu dependencies from Duet. Tiny can be offline for its first
HA installation and later updates. The signed courier also upgrades its managed
bootstrap automatically after the initial authorization. The dependency base is
pinned to the fork's tested version and digest-locked per bundle, with backup/health
validation on Tiny. It must not follow upstream `stable` independently of the fork.

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

## Dashboard-first startup and timing

The current Car Assistant build opens the native saved Car starter layout first.
It no longer waits for database migrations, GPS/OBD data or the reporting service.
Unavailable car readings stay unavailable until fresh data arrives. Maps, history
and other cards finish loading afterward; history and accounts are preserved.
The default profile skips household discovery, energy/prediction, media/streaming,
mobile-app services, scenes/tags and unused household helpers. Explicitly configured
optional integrations remain supported.

Normal signed updates apply this automatically, including removing the container's
startup dependency on the reporting service. No dashboard reset or owner setup is
needed. An already-running instance needs one restart to use the new runtime;
installation handles that. The first derived-image build is installation work,
not something repeated on every boot.

The Tiny still has to boot Ubuntu, start Docker and accept the Duet's authenticated
SSH tunnel. A completely powered-off Tiny cannot serve its saved dashboard.
Measure the whole car cold start with a stopwatch/video from power application to
the visible layout, then to fresh GPS/OBD readings. These are separate milestones.
For the browser portion, open its developer console after Car starter appears:

```js
performance.getEntriesByName("car-dashboard-visible").map(entry => ({
  dashboardVisibleSeconds: entry.startTime / 1000
}))
```

This measures navigation to first dashboard reveal, including browser auth and
module loading, not firmware/OS boot. An absent marker means a previous frontend
build or a different initial panel. Compare identical cold boots, then inspect
Tiny's journal with monotonic times to locate earlier delays:

```sh
sudo journalctl -b -o short-monotonic -u docker -u car-home-assistant --no-pager
```

The cloud test intentionally starts the reporting API only after authenticated
native dashboard edits have succeeded. Optional real-browser validation also
withholds `recorder/info` while checking layout visibility and the editor.

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

**Car starter** is an editable native storage dashboard, preconfigured with gauges,
connection status and the remaining entities. Open **Car starter** from the sidebar,
then its pencil / **Edit dashboard** control to add, remove, move or edit cards.
Overview in newer HA versions may offer only personalization/summaries; use Car
starter for the full card editor.

Existing managed YAML starters are migrated once, at the same `car-starter` URL,
using Home Assistant's Store API while HA is stopped. The template's cards/layout
are retained. The dashboard registry and this dashboard's storage are snapshotted
for rollback; other dashboards and owner/authentication storage are preserved.
Once migrated, updates do not reseed or overwrite your edited dashboard, or
recreate it after you deliberately delete it. The YAML file remains a template
for fresh installations, not the live dashboard.

If you have hand-edited `configuration.yaml` so it still declares the old
`lovelace.dashboards.car-starter` YAML entry, that custom configuration is preserved
and migration reports the conflict instead of rewriting unrelated settings.
Remove just that old YAML dashboard declaration and rerun normal reconciliation
when intentionally converting such a customized installation.

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
Native dashboard edits, owner accounts, tokens and history are preserved. The
one-time Car starter migration adds only its native dashboard and registry entry;
it never opens authentication storage. If you customize `configuration.yaml`, keep its
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

### Duet waiting for the Home Assistant SSH tunnel

An SSH connection opened before HA setup retains the old `permitopen` limits,
even after the installer adds port 8123 to its display key. The current proxy
reconnects its owned display tunnel automatically when Tiny reports HA ready but
the local forward fails, at most once per minute. This does not signal sshd,
Tailscale, administrator sessions or arbitrary SSH clients. The existing launcher
reconnect loop opens a fresh authenticated session with the updated permissions.

For an already-running older proxy, after pulling current code on Duet you can
request the same narrowly scoped recovery from an SSH terminal:

```sh
python3 duet/proxy.py --refresh-ha-tunnel
```

This uses the desktop user's account; no sudo or graphical-session variables are
needed. A nonzero `displayTunnelsReconnecting` count means reconnection was
requested, not proof HA has loaded. If the count is zero or waiting continues,
inspect `~/.local/state/drive-console/kiosk.log` and the response from
`http://127.0.0.1:8878/manifest.json`; the port may be absent or occupied by another
application. Key restrictions and host-key verification remain enabled.
