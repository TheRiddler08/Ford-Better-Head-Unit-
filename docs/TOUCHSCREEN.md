# Touchscreen cockpit v2

The Drive Console interface is a local HTML/CSS/JavaScript dashboard embedded inside the existing Python HTTP server. Its design takes inspiration from glanceable modern in-car cockpit layouts, while remaining grounded in **real recorded OBD2 values**.

## Home

The first two large dials show **latest recorded speed** and **engine RPM**. The colored arcs are *not* live ECU commands; the dashboard fetches the most recent persisted data from the recorder every three seconds.

The three additional cards display:
- Coolant temperature, in degrees F or C;
- Control-module voltage;
- Reported fuel level, with a warning that raw OBD fuel values can be incorrect (including isolated zero samples).

The status banner explicitly distinguishes **recent readings**, **stale data** and **missing database data**. If the recorder isn't running, the last saved values remain visible for historical context; always check their timestamps before using them.

Below the dials are engine load, intake temperature, a timeline, recorder counters, and sensor-data notices. The other pages retain searchable sensors, approximate drive sessions and diagnostic snapshots.

## Controls

- **Day/night:** tap the sun/moon icon in the header; the choice is saved in the browser profile.
- **Fullscreen:** tap the expand icon. In kiosk mode the browser is already fullscreen.
- **Refresh:** manually fetch current data and refresh history.
- **Units:** choose MPH/°F or km/h/°C.
- **Navigation rail:** select Home, Sensors, Trips or Health. On small screens it becomes a bottom bar.
- **Keyboard:** standard Tab, Enter and Space work for buttons; settings remain keyboard accessible.

The dashboard is offline-first: no CDN, tracking, external maps or internet is needed. The UI needs the local Python dashboard service and a readable SQLite database to display actual vehicle data.

## Developer tests

```bash
python3 -m unittest discover -s tests -v
node --check app/web/app.js
```

The Python suite validates that every statically named DOM element referenced by JavaScript is present, that the routes exist, and that the UI resources are local. GitHub Actions executes these checks on new commits and pull requests.

## Safety

Only glanceable gauge values should be used while operating the vehicle. Explore the tables, sensor filters and diagnostic pages while parked. The dashboard is for supplemental information, **not** a substitute for the factory speedometer, temperature indicators, dashboard warning lamps or professional automotive diagnostics.

Future audio/navigation/climate functionality must be implemented and tested against actual interfaces. This UI does not pretend those systems work before they exist.
