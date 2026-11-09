# Every car feature in the native HA dashboard

Open **Drive Console** at `/drive-console` in Car Assistant on the Tiny. It keeps
Home Assistant's native storage dashboard, independent editable views and card
editors. New system features are **Drive Console services** cards, alongside the
existing driving widgets. Each service card has a feature selector, title,
refresh interval, height and optional scoped CSS in its native editor. The
HTML/CSS/JavaScript widget remains available for custom sandboxed displays.

| Native tab | Features |
| --- | --- |
| Home | Clock, local media, map, vehicle illustration, speed/RPM and quick metrics |
| Navigation | Leaflet mouse/touch map, markers, waypoints, local routing requests, full-USA defaults, saved trips, manual departure/arrival, history replay and exports |
| Media | Browser-local music selection, playback, seek and previous/next controls |
| Vehicle | Read-only OBD values with freshness; user-editable artwork and metrics |
| Health | GPS recorder state, network, archive worker, radio, software-link observations, USB identities, a private-data-free health download and future-hardware notes |
| Updates | Duet download/delivery/install status, signed-source receipts and Park-gated reboot status |
| Settings | Vehicle profile, display preferences, saved places, scheduled departures, and the existing native card/page editors |
| Devices | Remembered versus recently observed Ethernet peers, gateway election, reported role services, laptop power policy and wake limitations |
| Storage | MooseFS mount/configuration/replica goal, archive counts, local reserve, selected-file editor and conflict choices |
| Recordings | G980H channel names/catalog, range-streamed local playback over IP |
| Radio | Adapter/key-pin status, encrypted round-trip request, message inbox/composer, health requests and local retry cancellation |
| Reports | Recorded sensor values, requests, snapshots, events, GPS fixes/raw messages/bytes/sessions/stops; cursor pages and complete streaming CSV exports |
| Home control | Authenticated home status, signed transfer history/details/priority/pause/resume/IP-only/retry, source-copy editor, selected-file studio, dashboard project JSON/HTML drafts/publication/restoration, versioned settings, coverage evidence and home catalogs |

The Home control card uses the existing restricted Tiny-to-home SSH tunnel.
Enter the **separate home owner panel token** locally in that card. It is kept in
card memory and cleared on disconnect/Forget; it is never stored in HA card YAML,
an entity, browser storage, a query string or radio traffic. The token is sent
only to the fixed loopback tunnel endpoint. Administrator permission is required
for home-owner access and local mutations. A missing home tunnel is shown as
pending; it does not block local driving, maps, media or archives.

Custom dashboard projects in Home control use the existing validated project
schema. Edit the project's HTML/CSS/JS fields in its advanced JSON, save the
draft, then publish it. Source changes keep their allowlist, Ed25519 signatures,
hash checks and safe activation path. No generic root commands, forced sleep,
ECU writes, fake acknowledgments or arbitrary proxy targets are added to HA.

## Preserving edits during upgrade

Dashboard schema 3 adds the new feature tabs once and appends the relevant new
service card to existing Health, Updates and Settings views. It preserves their
cards, titles, user-added views, accounts, resources and the separate Car starter.
A private managed-store backup is made before this migration and restored if
startup fails. A previously deleted old tab is not recreated. Deleting a new
feature tab/card after migration is also respected on later updates. You can
add it back yourself through the native editor.

The dashboard does not wait for GPS, OBD, storage, radio, home connectivity or
history migrations before showing its saved layout. Slow/failing service cards
report their own pending condition. Reading a report, editing a file or playing
a video is not interrupted by automatic card refresh.

## Everyday use and recovery

- **Health** includes a prioritized checklist with links to the relevant native
  tabs. It distinguishes pending setup, stale observations, low local disk reserve,
  adapter heartbeats and a matching encrypted radio echo. A historical passing
  radio test is not a guarantee of current reachability.
  GPS distinguishes a receiver waiting for its first fix from a disconnected
  receiver; neither state invents a coordinate or blocks map planning.
- Service cards show when they last read data, retain the last display on a
  failed refresh, and identify lost HA connectivity. Refresh intervals honor the
  card editor; hidden browser tabs do not keep polling. Refresh retries a failed
  read without using its old cache. In-flight reads cannot overwrite a card after
  changing its feature or removing it.
- **Settings** has forms for places, departure times and weekdays, with optional
  advanced JSON. Saving is explicit; removing a place used by a departure is
  blocked until you change that departure. These are local plans, not automatic
  driving commands. Concurrent saves from HA and the original kiosk cannot both
  overwrite the same preferences revision.
  Display/unit/clock preferences here belong to the original console. Native HA
  appearance and units remain independent in each card's editor.
- **Reports** supports local date/time and exact-name filters. The same filters
  apply to the complete CSV export; changing them starts a fresh cursor. The
  built-in Reports button stays inside HA, including on a remote laptop.
- **Recordings** can download an original clip or renew a temporary playback URL.
  Pausing a chosen video keeps the player; Close playback releases it and resumes
  catalog refresh. Browser codec support is still required for playback.
- Home source/config/file editors retain unsaved text on errors and track new
  saved revisions. A queued source edit locks that copy until you reload a newly
  reported device version. Forget clears the owner token immediately, even during
  a request; changing HA users also clears it. Forget cannot recall an already
  authorized request that reached home.
- If home MQTT state collection fails, the home bridge publishes unavailable and
  retries discovery before announcing recovery. Existing last-reported sensor
  freshness and the encrypted radio boundary remain in effect.

## Runtime and hardware limits remain explicit

- Local GPS history stays readable. LoRaWAN application traffic continues to use
  the existing public/private-key encrypted boundary. GPS radio reporting remains
  deferred. Hardware absence is not simulated or presented as healthy.
- Full-USA is the map/routing default; the actual offline map/routing pack still
  needs to be prepared/imported. Leaflet ships locally with its BSD license and
  integrity metadata. Online USGS tiles are a fallback, not a downloaded full-USA
  offline pack. Route calculation stays unavailable until a real local routing
  pack/engine is ready.
- G980H imports retain original names and channel folders. Direct Wi-Fi camera
  fetching remains pending a verified protocol. Video and complete CSV exports
  stream over the LAN/IP, using HA's scoped temporary download URLs; they are
  never loaded into a radio patch or buffered as a complete video in memory.
- Storage uses MooseFS. Archive writes pin the actual mounted filesystem for
  every role, preventing a disconnect from writing into an empty local mountpoint.
  A replica goal is a configuration target, not proof that both copies exist.
  The Pi remains a client with no bulk SD fallback; active SQLite/WAL files stay
  local, and originals are retained.
- OBD refuses a serial path that resolves to the configured LoRa radio or an
  identified u-blox receiver. CH340 alone does not identify an OBD adapter.
  Select the actual OBD device explicitly rather than probing arbitrary ports.
- Peer health beacons are small status observations on the trusted car switch.
  They do not authorize power actions. Deep sleep still needs confirmed car
  power loss and supported hardware; network loss alone never means car-off.
- USB-camera/Pi 5 AI, home-server AI offload and gateway mesh remain planning
  notes. No model, camera driver, video upload or mesh feature is silently enabled.

Home Home Assistant on the BTT Pi continues to use the existing MQTT integration
for last-reported sensors, freshness, update progress/paused counts and data-sync
state. These are separate from the local Car Assistant interface.

## Deploy and verify

Car updates still go through the **Duet courier**:

```bash
cd ~/car-dashboard
git pull --ff-only && bash duet/update.sh cayden@172.16.42.2
```

Update the home server separately with `git pull --ff-only && bash install.sh home`
from its checkout. After the Tiny reconciles its managed HA assets, reload the
HA browser to load the versioned module. Do not reset accounts or delete dashboard
storage. Device setup and privileged update verification remain on the established
normal installation paths.

Open each native tab. A real GPS fix, OBD adapter, mounted pool, camera import,
home broker and LoRaWAN receipt require physical validation on the car/home
hardware; passing the automated tests does not establish those conditions.
