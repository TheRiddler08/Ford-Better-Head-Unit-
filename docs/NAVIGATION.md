# Navigation, places and journeys

The map remains usable with GPS disconnected. GPS controls only the live vehicle
position and connection indicator, not map availability, markers, saved places,
manual trips, history or road-route planning. There is no application simulation
mode: external GPS/ELM serial sources feed the normal recorders and HA entities.
Linux still exposes those sources as PTYs; this is protocol virtualization, not
kernel USB hardware identity emulation.

## Map and trip controls

On any map card, expand **Places, routes & trips**.

- Drag with a mouse or one finger, scroll to zoom, use +/- or pinch with two
  fingers. **Follow GPS** / the locate button restores GPS following; when no fix
  exists, the saved default position is used. Deliberate panning remains possible.
- Choose **Inspect coordinates**, **Add marker** or **Add route waypoint**, then
  click/touch the map. Numeric latitude/longitude entry is also supported.
- Give markers names and colors; view, edit, delete or use a saved marker as a
  waypoint. Markers and waypoints are editable with GPS disconnected.
- Add at least two route waypoints. Reorder or remove them, then **Calculate road
  route**. Only the local OSRM engine's road geometry, distance, duration and turn
  list are shown. When data/engine coverage is missing, waypoints remain saved;
  no straight line is represented as a calculated road route or ETA.
- Save a planned or completed trip using route endpoints and via waypoints, a name, departure,
  arrival and notes. Times are entered in browser local time and stored as ISO
  timestamps with timezone information. Edit them later; completed trips need not
  have a recorded GPS track. These entries do not rewrite raw GPS records.
- Load recorded GPS by the departure/arrival range, or the last 24 hours when
  unset. **Load more** pages through records. Use the seek slider, replay speed
  and play/pause controls. Replay displays recorded fixes without interpolation;
  recorded segments/gaps remain separate. **Use history endpoints for trip**
  fills endpoint coordinates and times for a manually saved journey.
- Set **Default map position**, zoom and **Use current map view**, then save.
  Defaults are a generic USA centre, not a location inferred from GPS test data.
- **Routing pack regions** defaults to **us** (full USA). Smaller state packs are
  optional settings, not an assumption about your physical location.
- Export/import places, trips and route plans as JSON. Storage is authenticated
  and persistent on the Tiny using HA's native Store. Revision checks reject
  conflicting saves from another browser window instead of overwriting them.

Saved marker/trip/route data is shared by map cards in the current HA instance;
card appearance and native dashboard tabs remain independently editable. The
navigation data is not shipped over LoRaWAN. No drive/ECU control is performed.
Scheduled times are planning entries, not automatic vehicle actions. The route
turn list is not spoken turn-by-turn guidance or a traffic service.

## USA map imagery

The default online fallback uses **USGS The National Map / USGSTopo**, with visible
attribution and ordinary browser HTTP caching. It requests only the viewed area,
not the whole world or a bulk offline download. High zooms overzoom source tiles
past level 16 rather than claiming additional detail. Internet/provider access
is needed. **Online fallback** can be disabled in the native map card editor.
Custom raster tile-folder URLs and `{z}/{x}/{y}` URL templates (including provider query parameters) continue to work. Explicit card zoom overrides take priority over the shared default zoom when loading a card.

For offline imagery, install a raster PNG/JPEG MBTiles pack at:

```text
/var/lib/car-dashboard/maps/usa.mbtiles
```

Or set `CAR_MAP_MBTILES` in the normal dashboard environment to another local
pack. The existing XYZ directory `/var/lib/car-dashboard/tiles/{z}/{x}/{y}.png`
takes precedence. MBTiles TMS rows are converted to XYZ requests. Files are opened
read-only, not created on missing paths. Vector/PBF MBTiles need a vector renderer
and are not accepted as PNG images. Obtain a pack whose license permits your
storage/use; the application does not scrape public OSM tiles into an offline
pack. Tiles are served through authenticated HA requests on the existing tunnel.

## Full-USA road routing

**Imagery and routing are different data.** Raster map tiles contain pictures;
OSRM requires an OpenStreetMap road-graph pack. A full-USA pack is the default
selection but is not bundled in a code update. Download/preprocessing may require
many hours, substantial SSD space and a larger build host. The setup helper
refuses a full-USA build below 32 GB RAM or 150 GB free disk; actual peak needs
can be much larger. This limit does not prevent serving a prepared pack on a
smaller machine with memory-mapped files, although performance needs testing on
that machine. The previously reported 14 GiB server is below the build check.

The helper is installed automatically on the Tiny at
`/usr/local/lib/car-dashboard/routing_setup.py`. From this repository on a suitably
sized Linux/Docker build machine:

```sh
sudo python3 dev/setup_us_routing.py --region us \
  --directory /srv/car-routing-us --export-image
```

This downloads the official Geofabrik USA PBF over HTTPS, checks its publisher
checksum, uses a pinned official OSRM v6 image, runs extraction/partitioning/
customization, and starts a loopback-only engine on port 5000. Image metadata and
optional carried image archive are saved beside the prepared data. A list such
as `--region virginia maryland` is optional; merging multiple states needs
`osmium-tool`, included by the normal Ubuntu Tiny dependency installation.
The selection is explicit; GPS positions never choose the pack.

Carry the prepared directory to the Tiny. Keep all `region.osrm*` files,
`prepared.json`, and `osrm-image.tar` if the Tiny will have no Internet. The
original downloaded PBF/state files are not required for serving. Import into
`/var/lib/car-dashboard/routing` without overwriting an active dataset in place;
keep a backup of any previous pack. Start it with:

```sh
sudo python3 /usr/local/lib/car-dashboard/routing_setup.py \
  --serve-only --offline --region us \
  --directory /var/lib/car-dashboard/routing
```

The carried image is checksum-checked and its config identity verified before
use. Use only packs/runtime archives prepared by you or a trusted operator.
The container has no extra capabilities, reads its data mount read-only, uses
mmap, restarts automatically and publishes only `127.0.0.1:5000`. The setup helper
checks local HTTP readiness. Normal role updates activate an already prepared,
cached/carried runtime automatically. Missing data/runtime remains a reported
pending condition in `/etc/car-dashboard/navigation-status.json`; it does not
block GPS, the dashboard or saved plans. Docker databases and LoRaWAN transports
are not used for these packs. No public road-routing demo server is used.

## Independent manual input devices

GPS route generator, laptop provider bridge and manual GPS values are external
source tools; see [Virtual GPS](VIRTUAL_GPS.md). A manually controlled source:

```sh
cat > "$HOME/gps-values.json" <<'JSON'
{"lat":40,"lon":-100,"speed":0,"heading":0,"altitude":100}
JSON
sudo python3 /usr/local/lib/car-dashboard/virtual_gps.py \
  --replace-system-gpsd --values "$HOME/gps-values.json" --seconds 1800
```

Edit the JSON while it runs. Values become ordinary NMEA fixes. An exact fixed
point can alternatively use `--fixed --latitude 40 --longitude -100 --speed 0`.

OBD is a separate ELM327-compatible PTY, not a browser-gauge override:

```sh
cat > "$HOME/obd-values.json" <<'JSON'
{"speed":50,"rpm":1800,"coolant":85,"voltage":13.8,"fuel":70,"load":20,"throttle":10,"intake":25}
JSON
sudo python3 /usr/local/lib/car-dashboard/virtual_obd.py \
  --replace-system-obd --values "$HOME/obd-values.json" --seconds 1800
```

Speed is km/h, RPM is raw RPM, temperatures are Celsius, voltage is volts, the
remaining fields are percentages. Values are validated and reread on each
request. The unchanged python-OBD recorder discovers supported standard PIDs,
records readings and feeds the ordinary HA sensors. Source controllers do not
write readings directly, add simulation metadata, alter application values or
open a physical USB device. Their own terminal/files identify them as source
tools; the application has no alternate fake-data handling. Data enters normal
history and may reach other normally configured consumers.

The OBD controller temporarily selects its port using a runtime systemd drop-in,
then restores the previous active/inactive service state on exit. It does not
change the persistent real-adapter binding. Stop with Ctrl+C; each source also
has a finite timeout. Do not run a second source controller for the same device.
For isolated integration tests, omit system replacement, use the printed PTY as
`OBD_PORT`, and point the normal recorder at a disposable `CAR_DB`.

## Verification and limits

Automated checks cover authenticated HA navigation persistence, validation and
conflicts; disconnected-GPS planning; marker/waypoint edits; manual trip times;
default-position persistence; history replay and gap preservation; mouse and
pointer interactions; projection round trips; read-only MBTiles lookup; and an
unchanged python-OBD recorder consuming and updating external serial values.
A real OSRM v6 smoke test exercises graph extraction, partition/customization and
mmap startup on a controlled small road graph. Native Car Assistant startup and
authenticated navigation Store operations are tested inside the pinned runtime.

Cloud network policy blocks live USGS/Geofabrik access. Live provider behavior,
full-USA pack preparation/size, and performance on your actual hardware remain
deployment checks. Browser map tests use controlled tile responses; they are not
evidence that a USA offline imagery/routing pack is already installed.
