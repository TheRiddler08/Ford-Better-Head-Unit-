# Navigation map layers

Open the **Navigation** map and expand **Layers** at its upper right. These are
independent switches; the underlying map, saved places, routes and trips remain
usable without GPS. Choices are saved with navigation settings and shared
between the Home and Navigation map cards. Existing HA cards/views are preserved.

## Reported camera locations

Enable **Reported camera locations**. The default filter shows Flock Safety;
choose **All reported ALPR cameras** for other/unknown manufacturers. At low
zoom, numbered clusters keep the display responsive. Tap a cluster to zoom in.
Tap an individual report for its manufacturer/operator, reported facing
direction, source OpenStreetMap link and **Use as route waypoint** action.
Imported text is rendered as text, not executable HTML.

The normal signed software update includes a compact USA snapshot: 61,768 public
reports, source snapshot **2026-10-08**, with its checksum/provenance in
`data/map-layers/snapshot.json`. The Tiny's background worker seeds it locally
without waiting for Internet or blocking dashboard startup. The source date is
displayed separately from download time. Reports can be incomplete, inaccurate,
removed or outdated; these are not live camera feeds or activity detections.

The worker checks the fixed upstream DeFlock/FlockHopper USA export daily over
HTTPS. Failed downloads retain the previous usable snapshot and back off for an
hour. **Refresh cached data** requests another check; failed/empty/malformed
snapshots never erase a useful cache. No GPS, viewport, route or destination is
sent to the public data source. There is no per-pan public API lookup.

For a different local snapshot, use **Import camera JSON / GeoJSON** as an HA
administrator. Supported formats are Point GeoJSON FeatureCollections, OSM
`elements` JSON and the DeFlock/FlockHopper camera array. Files must be under
16 MB; invalid coordinates/oversized exports are rejected atomically. Imports
stay local, do not edit OpenStreetMap, and are retained until another import or
explicit public refresh. The public camera database is © OpenStreetMap
contributors, ODbL; attribution/source links remain available on the map.

**Nearby-camera alerts** are optional visual notices with a configurable
50–5,000 m distance. They use the normal HA GPS tracker, including an independent
virtual serial GPS source. Alerts require a current fix, are calculated against
the local cache even when browsing a different area, and do not use the default
map center as a vehicle position. Browser/background restrictions still apply;
this is not an operating-system notification service.

## LoRaWAN reception evidence

Enable **Recorded LoRaWAN receptions**. Each dot is a positive, time-stamped
reception received by a registered fixed gateway. Tap it for gateway EUI,
RSSI/SNR and time. Green dots are RSSI ≥ −90 dBm, amber ≥ −110 dBm, red weaker,
and grey missing RSSI. These are display bands, not a connectivity guarantee.
Untested locations and missing uplinks remain unknown. No polygon fills the
space between reception points.

The home server's existing encrypted radio ingestion remains the source. The
Tiny polls an authenticated, read-only device endpoint through its existing
pinned SSH home tunnel/HTTPS connection, caches paginated evidence locally and
continues displaying it offline. Raw packets and credentials are excluded from
the feed. Unknown/mobile gateway roles, stale GPS/mode samples and explicitly
ineligible receptions never become fixed-site coverage. Local gateway edits
cannot promote a home-classified mobile gateway's samples into that layer.

Use **Home server → Register fixed/mobile gateways**, authenticate with the
existing separate owner panel token, and register each actual gateway EUI/role.
This owner-only registry controls eligibility of future receptions. The map's
local location editor controls display/estimate preferences; it does not grant
network-server eligibility or change a gateway's radio configuration. An unknown
role can exclude a site without deleting its reception audit history.

This update does **not** enable deferred GPS radio reporting or generate probe
transmissions. Until eligible GPS-tagged receptions already exist on home, the
layer truthfully remains empty. A completed radio echo alone supplies no GPS
reception coordinates. It does not reveal missed packets or current RF range.

Update the home server too so its new device endpoint is available:

```sh
cd ~/car-dashboard
git pull --ff-only
bash install.sh home
```

An unavailable/older home server leaves the previous local evidence intact.
Device-token authentication is separate from the home owner's panel token.

## Gateway locations and estimate rings

Expand **Gateway locations & estimates**. Home registry locations are imported
when present; otherwise enter a 16-digit gateway EUI, name, fixed/mobile role
and coordinates, or choose **Use selected map point** after selecting a point.
Locations are explicit; the current vehicle/default GPS point is never assumed
to be a fixed gateway. Select an existing gateway to edit or hide it. Local
location/name/radius edits and hidden-site choices survive home synchronization.

Enable **Estimated gateway range** for dashed circles around fixed sites with a
user-specified radius. Radius defaults to zero, so no imaginary range is shown
before configuration. Rings are estimates, not measurements: terrain, antenna
gain, buildings, interference and link availability are not modeled. Mobile
gateways get a marker but no fixed-site range circle. The gateway selector
filters sites and measured points; opacity adjusts the overlay transparency.

## Installation, storage and checks

Car updates still go through the Duet's normal signed courier. Normal Tiny role
setup enables the unprivileged `car-map-layers` background service. Other roles
may receive the 1.4 MB compressed snapshot with their software checkout, but
only Tiny expands, caches and refreshes it; Pi does not add a dataset worker or
microSD archive contribution. All
camera/cache traffic uses IP/Ethernet; it never enters LoRaWAN queues.

The local cache is a private SQLite/WAL file at
`~/.local/share/drive-console/map-layers.sqlite3` on Tiny. It contains public
camera reports **and private reception positions**; it is not a public database
export. Public imports are limited to 200,000 cameras; the reception display
cache retains up to 200,000 latest records while the home history remains the
source. Viewport responses are bounded and ask users to zoom/filter when needed.

Set `"map_layers": false` in root-owned `/etc/car-dashboard/fleet.json` to stop
the worker, or `"map_camera_download": false` to retain bundled/imported camera
data without public refreshes. Existing opt-outs and radio authentication stay
unchanged. Read-only map viewing requires HA authentication; imports, refreshes
and gateway mutations require an HA administrator. No public URL is accepted by
the map API. Normal navigation exports include layer choices, not the bulk cache.

Validation includes local imports/clustering/nearby queries, cache retention,
fixed/mobile gateway eligibility, device/HA authentication, default installation,
and a real Leaflet browser test for saved toggles, safe popups, offline operation
and waypoint/alert behavior. Actual RF range and physical hardware are unverified.
