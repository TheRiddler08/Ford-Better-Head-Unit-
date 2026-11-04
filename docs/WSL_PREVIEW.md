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
packaged Car Assistant fork, seeds its native editable Car starter dashboard, then
starts it. The first download/build can take several minutes and is excluded from
the printed startup timing. No frontend/Node build or Python package installation
is needed on WSL. The same command also works on a Linux Docker installation.

Open **http://localhost:18123/car-starter** in your Windows browser. Create a
separate test owner account using the normal first-run screens. Confirm the
preview's HTTP settings if prompted. The exact native editor and cards are
available; GPS/OBD show unavailable because the car's reporting service is absent.

The preview creates only a labeled Docker container and named configuration volume.
It publishes port 18123 on host loopback. Its container HTTP listener uses 0.0.0.0
so Docker's port mapping can reach it. Car networking, power services, Tailscale,
updates and attached vehicle hardware are not configured on the WSL machine.

## Repeat startup measurements

After initial owner setup:

```sh
python3 dev/assistant_preview.py measure
```

This stops and starts the existing container, keeping the test account and layout.
It prints time from the container-start request until the frontend manifest answers
HTTP. It does not download, rebuild, reset accounts or erase dashboards.

For navigation-to-visible-dashboard timing, refresh the browser on Car starter,
then open its developer console (F12) and run:

```js
performance.getEntriesByName("car-dashboard-visible").map(entry => ({
  dashboardVisibleSeconds: entry.startTime / 1000
}))
```

Container readiness, first browser navigation, cached navigation and fresh vehicle
readings are different milestones. WSL measurements do not include Windows/WSL
boot or predict the Tiny's firmware, Ubuntu and Ethernet/SSH cold-start time.

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
