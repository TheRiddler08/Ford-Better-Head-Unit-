# Car display studio

The home control panel includes a multi-page visual builder and HTML/CSS/JavaScript
workspace. Published projects travel through the existing SSH/Tailscale polling
connection, independently of the small settings queue. LoRaWAN is not used for
these larger documents.

## Update an existing installation

On the home server, as the account running the panel:

```sh
cd ~/car-dashboard
git pull --ff-only
systemctl --user restart car-home-panel
```

On the car, as its dashboard user:

```sh
cd ~/car-dashboard
git pull --ff-only
systemctl --user restart car-home-sync
sudo systemctl restart car-dashboard
```

Both ends need the new code. Existing tokens, SSH forwarding, and systemd units
are reused. The agent saves `layout.json` beside the configured `CAR_UI_CONFIG`;
the dashboard reads that same path. If you customized paths, set `CAR_LAYOUT_FILE`
consistently in both services. Keep this file writable by the sync user and
readable by the dashboard user. No new Python runtime packages are required.
For a first installation, follow [the Tailscale setup guide](TAILSCALE_HOME_CONTROL.md).

## Build visually

1. Open the existing home panel through your SSH tunnel and unlock it with the
   admin token. Confirm the device ID matches `CAR_DEVICE_NAME` (usually `car-1`).
2. In **Build your cockpit**, choose a telemetry, clock, or blank starter, or add
   components to the existing draft. Starter replacement can be undone.
3. Choose the canvas width and height for your screen. Zoom changes the editor
   view only; the car scales visual layouts to fit its display.
4. Add headings, text, panels, live sensors, clocks, embedded images, page buttons,
   or custom HTML blocks. Drag a component to position it; drag its lower-right
   handle to resize it. The inspector also accepts exact position, dimensions,
   colors, font size, and content. Layer controls change stacking order.
5. Add or rename pages. Page buttons use the target page ID shown in the page
   selector. Multiple pages also get a built-in navigation row.
6. Enable **Interact with preview** to test buttons; disable it to select and drag.
   Preview readings are marked **PREVIEW SAMPLE**, not real vehicle telemetry.
7. **Save draft** keeps your work on the home server without changing the car.
   Undo/redo retains up to 60 editing steps for the current session. Export the
   project as JSON for an independent backup; import validates it before loading.
8. **Publish to car** creates a new numbered version. The panel distinguishes
   publication from the car's save acknowledgment. Normally delivery takes about
   15 seconds and status refreshes every 10 seconds; an offline car retains its
   current layout until it reconnects.

Drafts require manual saving; leaving with unsaved edits warns in the browser.
Changes from another tab cause a version conflict instead of overwriting work.
The last 20 published versions can be restored. Restoring creates a new increasing
revision, which the car can safely accept.

## HTML, CSS and JavaScript

CSS and JavaScript apply to both visual and HTML modes. HTML mode accepts body
markup; use the CSS and JS panes for styles and scripts. **Edit generated HTML**
copies the visual markup into the HTML editor for unrestricted markup editing.
Switching back to visual mode uses the retained components; arbitrary edited HTML
does not automatically convert back into draggable components.

The renderer supplies `.layout-block`, `.layout-page`, `#layout-stage` and the
visual layout's baseline styles. You can override these in the CSS pane.
JavaScript runs in the document head; initialize DOM behavior in
`DOMContentLoaded`:

```html
<h1 id="greeting">My car</h1>
<strong data-sensor="RPM">—</strong>
<small data-sensor-status="RPM">No telemetry</small>
<div data-clock></div>
```

```js
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('greeting').textContent = 'Ready for the road';
});

window.addEventListener('cartelemetry', event => {
  const reading = event.detail.sensors?.RPM;
  // reading.number, reading.unit and reading.ts are the recorder's values.
  // Check freshness before presenting a measurement as current.
});
```

Sensor keys are the database's keys (for example `SPEED`, `RPM`, or `COOLANT_TEMP`).
Data is not automatically converted to US/metric units. `data-sensor-status`
marks readings older than 45 seconds as stale. In source mode include a status
indicator alongside your own gauges; JavaScript controls how your custom UI
presents that information. `window.carTelemetry` also holds the latest payload.

Projects are capped at 400 KB, with 12 pages and 80 components per page. The image
importer embeds local PNG/JPEG/WebP/GIF files up to 180 KB; the whole project still
must fit its limit. External scripts, stylesheets, fonts, images, frames, API
requests and form submissions are blocked. Embed assets as data URLs and keep
source self-contained. JavaScript can modify its own display but cannot access
panel tokens, parent DOM, system files, dashboard write APIs, or vehicle controls.
Do not use this builder to distribute native code, recorder patches or shell
commands.

## Activate and recover

An enabled published layout becomes the car's default `/` display. A standard
cockpit already open at `/` checks for it every 15 seconds. While a custom display
is open, new published versions load automatically. Car acknowledgment means the
layout was validated and saved, not that someone verified its appearance.

The fixed **Standard dashboard / Recovery** link lives outside the custom frame.
`/cockpit.html` always opens the original dashboard and does not automatically
switch back to the custom UI. **Use standard dashboard** in the home panel sends
an explicit disabled version to the car. You can also restore an earlier
published version. If home-to-car connectivity is unavailable, use the recovery
link locally; remote disable/restore must wait for reconnection.

Custom JavaScript runs in an iframe with `sandbox="allow-scripts"`, an opaque
origin, and its own restrictive content policy. The parent page sends only a
read-only telemetry payload. Browser isolation limits access, not script CPU or
memory use: preview scripts before publishing, and use the standard dashboard
if a custom script misbehaves. Invalid layout files fall back to the standard
cockpit on a fresh root-page load.

Home project/history state is in `CAR_HOME_STATE/layouts.sqlite3`. Back it up
along with existing home state and private configuration using your normal
secure backup procedure.

## Development verification

Run the standard library suite:

```sh
python3 -m unittest discover -s tests -v
```

An optional actual-browser integration test exercises visual dragging, HTML/CSS/JS,
sandbox restrictions, publishing, delivery, telemetry, mobile overflow and recovery:

```sh
# Requires the development-only Playwright Python package and Chromium.
CHROMIUM_PATH=/usr/bin/chromium python3 tests/browser_ui_builder.py
```

No Playwright dependency is required to run the application or standard tests.
Physical touchscreen rendering, tailnet connectivity, and service upgrades must
still be checked on the home and car machines.
