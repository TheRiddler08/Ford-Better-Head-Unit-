# Quick Car Assistant preview on WSL2

Use Docker Desktop with Linux containers and WSL2. In Docker Desktop, enable
**Settings → Resources → WSL integration** for your Ubuntu/Linux distribution.
`docker version` inside that WSL terminal should show a client and a server.
Git and Python 3 are the only other host requirements.

Keep the checkout in the Linux home directory for filesystem performance:

```sh
git clone --depth 1 https://github.com/TheRiddler08/Ford-Better-Head-Unit-.git ~/car-assistant-test
cd ~/car-assistant-test
python3 dev/assistant_preview.py start
```

The helper downloads the pinned dependency image once, verifies and builds the
packaged Car Assistant fork, seeds its native editable Drive Console dashboard, then
starts it. The first download/build can take several minutes and is excluded from
the printed startup timing. No frontend/Node build or Python package installation
is needed on WSL. The same command also works on a Linux Docker installation.

Open **http://localhost:18123/drive-console** in your Windows browser. Create a
separate test owner account using the normal first-run screens. Confirm the
preview's HTTP settings if prompted. The exact native editor and cards are
available; GPS/OBD show unavailable because the car's reporting service is absent.

The preview creates only a labeled Docker container and named configuration volume.
It publishes port 18123 on host loopback. Its container HTTP listener uses 0.0.0.0
so Docker's port mapping can reach it. Car networking, power services, Tailscale,
updates and attached vehicle hardware are not configured on the WSL machine.

## Edit each dashboard tab

Drive Console now has separate native Home/Drive, Navigation, Media, Vehicle,
Health, Updates and Settings views. The top tabs, sidebar and bottom launcher
navigate to real dashboard URLs, such as `/drive-console/navigation`. Each view
has its own saved cards and settings; changing Navigation does not change Home.

Click the pencil, select the tab to edit, then:

- Edit its **Page settings** card for cockpit/grid layout, columns, spacing,
  colors, typography, header, sidebar, tabs, bottom launcher and custom page CSS.
- Edit individual widgets for labels, colors, size, entity bindings, units, gauge
  ranges/precision, map text/zoom, music controls, vehicle imagery and custom CSS.
- Use the normal HA controls to add, move, duplicate or delete cards, badges and
  views. Native cards work alongside the Drive Console widgets.

**Save**, then **Done** returns to the car screen. A small pencil remains available
if you hide the header. On short landscape screens, compact bottom controls keep
the volume row visible; that option is configurable per page.

Sidebar and launcher item lists accept objects such as:

```yaml
- label: Navigation
  icon: mdi:map
  page: navigation
- label: Reports
  icon: mdi:chart-box
  url: http://127.0.0.1:8877/records
  new_tab: true
```

Leave a list blank to follow the native tabs automatically. Use an empty list to
show no items. Page CSS targets its header/sidebar/launcher; widget CSS targets
that widget's shadow DOM. Editing appearance does not add unsupported vehicle
controls or fabricate unavailable sensor readings.

The update adds missing default tabs once, keeps your existing Home/Drive cards
and custom pages, and backs up the pre-upgrade dashboard in its private storage.
Later module updates preserve edits and deliberately deleted tabs. Car starter,
accounts and other dashboards remain intact. Refresh the browser after updating
to load the latest versioned module; no runtime image rebuild is required.

Local music stays in the browser. Local tiles and the full health/update/report
service use the existing car backend on port 8877. Health and Updates are editable
native tabs with explicit buttons to those services, not automatic redirects.
The backend remains unavailable in an isolated WSL preview.

## Repeat startup measurements

After initial owner setup:

```sh
python3 dev/assistant_preview.py measure
```

This stops and starts the existing container, keeping the test account and layout.
It prints time from the container-start request until the frontend manifest answers
HTTP. It does not download, rebuild, reset accounts or erase dashboards.

For navigation-to-visible-dashboard timing, refresh the browser on Drive Console,
then open its developer console (F12) and run:

```js
performance.getEntriesByName("car-dashboard-visible").map(entry => ({
  dashboardVisibleSeconds: entry.startTime / 1000
}))
```

Container readiness, first browser navigation, cached navigation and fresh vehicle
readings are different milestones. WSL measurements do not include Windows/WSL
boot or predict the Tiny's firmware, Ubuntu and Ethernet/SSH cold-start time.

## Recover a stopped preview

Run `python3 dev/assistant_preview.py start` again. For an unchanged installation,
it repairs the Docker restart policy if needed and starts the existing container
without rebuilding or changing the saved account/layout. Home Assistant's internal
restart requests can exit the container, so the preview uses `unless-stopped` for
automatic recovery. The explicit `stop` command still keeps it stopped.

`status` reports the exit code, out-of-memory flag, restart policy and declared
port bindings, including while the container is stopped. A successful old startup
log does not mean that container is still running now.

## Stop, inspect or test a newer release

```sh
python3 dev/assistant_preview.py stop
python3 dev/assistant_preview.py status
docker logs --tail 60 car-assistant-preview
```

To apply a newer release:

```sh
git pull --ff-only
python3 dev/assistant_preview.py start
```

An unchanged running preview stays running. If the runtime or managed car assets
changed, the helper rebuilds/replaces only its preview container and retains its
named volume, accounts and edited dashboards. Manually customized configuration
files are preserved. The volume is `car-assistant-preview-config`; it remains after
stopping the container. Reusing an unrelated container or volume with those names
is refused.
