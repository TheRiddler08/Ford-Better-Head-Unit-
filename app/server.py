#!/usr/bin/env python3
"""Read-only vehicle telemetry dashboard. Python standard library only."""
import json
import logging
import os
import sqlite3
import socket
import re
import sys
import subprocess
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FLEET_FILE = Path("/etc/car-dashboard/fleet.json")
WEB_DIR = Path(__file__).resolve().parent / "web"
DB_PATH = Path(os.environ.get("CAR_DB", str(Path.home() / "car-console/obd-data/vehicle.sqlite3"))).expanduser()
HOST = os.environ.get("CAR_BIND", "127.0.0.1")
PORT = int(os.environ.get("CAR_PORT", "8787"))
TIME_RANGES = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}
LOG = logging.getLogger("car-dashboard")
TILE_ROOT = Path(os.environ.get("CAR_MAP_TILES", "/var/lib/car-dashboard/tiles"))
MAP_MBTILES = Path(os.environ.get('CAR_MAP_MBTILES','/var/lib/car-dashboard/maps/usa.mbtiles'))
TILE_ROUTE = re.compile(r"^/tiles/([0-9]{1,2})/([0-9]{1,8})/([0-9]{1,8})\.png$")



sys.path.insert(0, str(WEB_DIR.parent.parent))
from app import reporting, ha_bridge
from control.config_core import read_config
from control.layouts import read_delivery, FRAME_HOST, FRAME_POLICY
from app.headunit_prefs import read as read_prefs, update as update_prefs

CONFIG_FILE = Path(os.environ.get("CAR_UI_CONFIG", "/etc/car-dashboard/ui-config.json"))
GPS_DB_PATH = Path(os.getenv("CAR_GPS_DB", str(DB_PATH.with_name("gps.sqlite3")))).expanduser()
LAYOUT_FILE = Path(os.getenv("CAR_LAYOUT_FILE", str(CONFIG_FILE.with_name("layout.json"))))
PREFS_FILE = Path(os.environ.get("CAR_HEADUNIT_PREFS", str(Path.home() / ".config/car-dashboard/preferences.json")))
OVERRIDE_DIR = Path(os.environ.get("CAR_OVERRIDE_DIR", "/var/lib/car-dashboard/overrides"))

def connect():
    """No file creation and no SQL writes; don't use immutable with a live recorder."""
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Recorder database not found: {DB_PATH}")
    db = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    db.execute("PRAGMA busy_timeout = 2000")
    return db


def rows(db, sql, args=()):
    return [dict(x) for x in db.execute(sql, args).fetchall()]


def table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def latest_readings(db, limit=1600):
    data = rows(db, "SELECT name, value, number, unit, ts FROM readings ORDER BY rowid DESC LIMIT ?", (limit,))
    latest = {}
    for r in data:
        name = r["name"]
        if name not in latest or float(r["ts"] or 0) > float(latest[name]["ts"] or 0):
            latest[name] = r
    return latest


def route_live(db):
    latest = latest_readings(db)
    last = max((float(r["ts"]) for r in latest.values() if r["ts"] is not None), default=None)
    now = time.time()
    age = round(now - last, 1) if last is not None else None
    return {"sensors": latest, "lastReading": last, "ageSeconds": age,
            "fresh": bool(age is not None and -20 <= age <= 45), "serverTime": now}


def route_summary(db):
    info = rows(db, "SELECT COUNT(*) total, COUNT(DISTINCT name) sensors, MIN(ts) first, MAX(ts) last FROM readings")[0]
    count = rows(db, "SELECT COUNT(*) count FROM snapshots")[0]["count"] if table_exists(db, "snapshots") else 0
    return {"totalReadings": info["total"], "sensorCount": info["sensors"],
            "firstReading": info["first"], "lastReading": info["last"], "snapshotCount": count}


def route_sensors(db):
    return rows(db, """SELECT name, COUNT(*) AS samples, MAX(ts) AS lastSeen,
                             MIN(number) AS min, MAX(number) AS max, AVG(number) AS average,
                             MAX(unit) AS unit
                      FROM readings GROUP BY name ORDER BY samples DESC, name""")


def route_history(db, query):
    name = (query.get("name") or ["SPEED"])[0][:100]
    period = (query.get("period") or ["24h"])[0]
    seconds = TIME_RANGES.get(period, TIME_RANGES["24h"])
    after = int(time.time()) - seconds
    bucket = max(1, (seconds + 299) // 300)
    data = rows(db, """SELECT CAST((ts - ?) / ? AS INTEGER) bucket,
                              MAX(ts) ts, AVG(number) number, COUNT(*) samples, MAX(unit) unit
                       FROM readings
                       WHERE name = ? AND ts >= ? AND number IS NOT NULL
                       GROUP BY bucket ORDER BY bucket""", (after, bucket, name, after))
    return {"name": name, "period": period, "points": data}


def route_snapshots(db):
    if not table_exists(db, "snapshots"):
        return []
    return rows(db, "SELECT id, stage, started, total, successes FROM snapshots ORDER BY id DESC LIMIT 60")


def route_events(db):
    if not table_exists(db, "events"):
        return []
    return rows(db, "SELECT ts, name, detail FROM events ORDER BY rowid DESC LIMIT 80")


def route_trips(db):
    # A 'session' is an RPM/speed logging cluster separated by > 5 minutes of silence.
    # It's not necessarily a drive; no assumptions about ignition/odometer/GPS.
    samples = rows(db, """SELECT ts, name, number FROM readings
                          WHERE name IN ('SPEED','RPM') AND number IS NOT NULL
                          ORDER BY ts, rowid""")
    sessions = []
    current = None
    prev_speed = None
    for r in samples:
        ts = float(r["ts"])
        value = float(r["number"])
        if current is None or ts - current["end"] > 300:
            current = {"start": ts, "end": ts, "speedSamples": 0, "rpmSamples": 0,
                       "maxSpeed": None, "maxRpm": None, "distanceKmEstimate": 0.0}
            sessions.append(current)
            prev_speed = None
        current["end"] = max(current["end"], ts)
        if r["name"] == "SPEED":
            current["speedSamples"] += 1
            current["maxSpeed"] = max(current["maxSpeed"] or 0, value)
            if prev_speed is not None:
                prev_ts, prev_kmh = prev_speed
                delta = ts - prev_ts
                if 0 < delta <= 30:
                    current["distanceKmEstimate"] += max(0, (prev_kmh + value) / 2 * delta / 3600)
            prev_speed = (ts, value)
        else:
            current["rpmSamples"] += 1
            current["maxRpm"] = max(current["maxRpm"] or 0, value)
    for item in sessions:
        item["durationSeconds"] = max(0, item["end"] - item["start"])
        item["distanceKmEstimate"] = round(item["distanceKmEstimate"], 2)
    return list(reversed(sessions[-100:]))


def route_alerts(db):
    fuel = rows(db, """SELECT ts, number FROM readings
                        WHERE name='FUEL_LEVEL' AND number IS NOT NULL
                        ORDER BY ts DESC LIMIT 30""")
    alerts = []
    for i, r in enumerate(fuel):
        if r["number"] is not None and float(r["number"]) <= 0:
            neighbors = [float(x["number"]) for x in fuel if x["number"] is not None
                         and 0 < abs(float(x["ts"]) - float(r["ts"])) < 3600]
            if any(x > 5 for x in neighbors):
                alerts.append({"code": "fuel_zero", "severity": "warning", "ts": r["ts"],
                               "message": "A 0% fuel reading appears near readings above 5%. Verify before using this as a fuel gauge."})
                break
    return alerts


def parse_gps_message(message):
    """Extract a real gpsd TPV 2D/3D position; never invent coordinates."""
    if not isinstance(message, dict) or message.get("class") != "TPV":
        return None
    mode = message.get("mode", 0)
    try:
        lat, lon = float(message["lat"]), float(message["lon"])
        mode = int(mode)
        if mode < 2 or not all(map(__import__("math").isfinite, (lat, lon))):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return {"fix": True, "lat": lat, "lon": lon, "mode": mode,
                "source": "gpsd", "time": message.get("time"),
                "speedMps": message.get("speed")}
    except (TypeError, ValueError, KeyError, OverflowError):
        return None


def query_gpsd():
    """One bounded localhost-only gpsd WATCH request, best effort."""
    result = {"fix": False, "source": "gpsd", "message": "No GNSS fix received"}
    try:
        deadline=time.monotonic()+1.5
        with socket.create_connection(("127.0.0.1", 2947), timeout=0.6) as sock:
            sock.settimeout(0.75)
            sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
            with sock.makefile("rb") as stream:
                for _ in range(12):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    sock.settimeout(min(.75,remaining))
                    try:
                        raw = stream.readline(8192)
                    except (TimeoutError, OSError):
                        break
                    if not raw:
                        break
                    try:
                        message = json.loads(raw.decode("utf-8"))
                    except (UnicodeError, ValueError):
                        continue
                    position = parse_gps_message(message)
                    if position is not None:
                        return position
                    if isinstance(message,dict) and message.get('class')=='TPV':
                        return dict(result,connected=True,message='GPS connected; waiting for a GNSS fix')
        return result
    except OSError:
        return {"fix": False, "source": "gpsd", "message": "GPS daemon not connected"}


def local_tile_path(path):
    """Resolve only numeric XYZ PNG tiles below the configured local tile folder."""
    matched = TILE_ROUTE.fullmatch(path)
    if not matched:
        return None
    z, x, y = map(int, matched.groups())
    if z < 0 or z > 19 or x >= 2 ** z or y >= 2 ** z:
        return None
    return TILE_ROOT / str(z) / str(x) / (str(y) + ".png")


ROUTES = {"/api/live": route_live, "/api/summary": route_summary,
          "/api/sensors": route_sensors, "/api/snapshots": route_snapshots,
          "/api/events": route_events, "/api/trips": route_trips,
          "/api/alerts": route_alerts}
STATIC = {"/update-watch.js": ("update-watch.js", "text/javascript; charset=utf-8"),"/": ("cockpit.html", "text/html; charset=utf-8"),
          "/cockpit.html": ("cockpit.html", "text/html; charset=utf-8"),
          "/classic.html": ("index.html", "text/html; charset=utf-8"),
          "/records": ("records.html", "text/html; charset=utf-8"),
          "/records.js": ("records.js", "text/javascript; charset=utf-8"),
          "/records.css": ("records.css", "text/css; charset=utf-8"),
          "/custom": ("custom.html", "text/html; charset=utf-8"),
          "/custom.js": ("custom.js", "text/javascript; charset=utf-8"),
          "/custom.css": ("custom.css", "text/css; charset=utf-8"),
          "/cockpit.css": ("cockpit.css", "text/css; charset=utf-8"),
          "/cockpit.js": ("cockpit.js", "text/javascript; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}


class Handler(BaseHTTPRequestHandler):
    server_version = "CarDashboard/1.0"

    def send_bytes(self, payload, status=200, mime="application/json; charset=utf-8", cache="no-store", policy=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", policy or "default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, allow_nan=False, default=str).encode(), status)

    def do_GET(self):
        if urlsplit(self.path).path=='/api/messages':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.send_json({'error':'Localhost required'},403)
            try:
                from control.messages import local_events,listing
                return self.send_json(listing(local_events(json.loads(FLEET_FILE.read_text()))))
            except (OSError,ValueError):return self.send_json({'error':'Application pairing required'},503)
        if urlsplit(self.path).path in ('/radio-messages','/radio-messages.js','/radio-messages.css'):
            suffix=Path(urlsplit(self.path).path).suffix
            path=WEB_DIR/'radio-messages.html' if not suffix else WEB_DIR.parent.parent/'control/web'/('radio-messages'+suffix)
            return self.send_bytes(path.read_bytes(),mime={'.js':'text/javascript','.css':'text/css','':'text/html; charset=utf-8'}[suffix])
        url = urlsplit(self.path)
        if url.path=='/api/map-layers':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.send_json({'error':'Localhost required'},403)
            try:
                from control.map_layers import location,query
                return self.send_json(query(location(),parse_qs(url.query)))
            except (ValueError,TypeError):return self.send_json({'error':'Invalid map layer query'},400)
            except (OSError,sqlite3.Error):return self.send_json({'error':'Map layer cache unavailable'},503)
        if url.path=='/api/system/status':
            from app.system_status import snapshot
            from app.system_status import read
            config=read(FLEET_FILE)
            return self.send_json(snapshot(config.get('home',Path.home()),config.get('role','unconfigured'),gps_db=GPS_DB_PATH))
        if url.path in ('/api/local-sync','/api/local-sync/file'):
            try:
                from control.file_sync import car_files
                config=json.loads(FLEET_FILE.read_text());options=json.loads(Path('/etc/car-dashboard/update-link.json').read_text());config.update(options)
                files=car_files(config)
                if url.path.endswith('/file'):return self.send_json(files.inspect(parse_qs(url.query).get('name',[''])[0]))
                files.scan();return self.send_json({'selected':sorted(files.selected),'items':files.status()})
            except (OSError,ValueError,KeyError) as error:return self.send_json({'error':'Local file sync is pending configuration'},503)
        if url.path=="/home-assistant":
            try:ha_status=json.loads(Path('/etc/car-dashboard/home-assistant.json').read_text())
            except (OSError,ValueError):ha_status={}
            if not isinstance(ha_status,dict) or not ha_status.get('installed') or not ha_status.get('enabled'):
                return self.send_bytes(b'<h1>Home Assistant is not installed or is disabled</h1><p>Run deploy/install-home-assistant.sh on the Tiny first.</p><a href="/cockpit.html">Return to standard dashboard</a>',mime='text/html; charset=utf-8')
            self.send_response(302)
            self.send_header('Location','http://127.0.0.1:8123/lovelace')
            self.send_header('Content-Length','0')
            self.end_headers()
            return
        if url.path=="/api/home-assistant/state":
            try:
                with connect() as db:live=route_live(db)
                sensors=live['sensors']
            except (OSError,ValueError,sqlite3.Error):sensors={}
            return self.send_json(ha_bridge.snapshot(sensors,query_gpsd()))
        if url.path=="/api/home-assistant":
            try:
                data=json.loads(Path('/etc/car-dashboard/home-assistant.json').read_text())
            except (OSError,ValueError):data={'installed':False,'enabled':False,'ready':False}
            return self.send_json(data)
        if url.path=="/api/car-storage":
            try:
                cfg=json.loads(FLEET_FILE.read_text())
                paths={"provisioning":Path('/var/lib/car-dashboard-fleet/storage.json'),
                       "worker":Path(cfg['home'])/'.local/share/car-data/status.json'}
                return self.send_json({name:json.loads(path.read_text()) if path.exists() else {'phase':'pending'} for name,path in paths.items()})
            except (OSError,ValueError,KeyError) as exc:return self.send_json({'error':str(exc)},503)
        if url.path in ("/api/dashcam","/api/dashcam/clip"):
            return self.camera_request(url)
        if url.path.startswith("/api/report/"):
            return self.report_request(url)
        if url.path=="/layout-host":
            return self.send_bytes(FRAME_HOST,mime="text/html; charset=utf-8",policy=FRAME_POLICY)
        if url.path=="/layout-runtime.js":
            return self.send_bytes((WEB_DIR.parent.parent/"control/web/layout-runtime.js").read_bytes(), mime="text/javascript; charset=utf-8")
        if url.path=="/api/layout":
            try:
                return self.send_json(read_delivery(LAYOUT_FILE))
            except (OSError,ValueError,TypeError):
                return self.send_json({"error":"Invalid custom layout"},503)
        if url.path in STATIC:
            filename, mime = STATIC[url.path]
            allowed = {"cockpit.html","cockpit.css","cockpit.js","index.html","styles.css","app.js","update-watch.js"}
            if url.path=="/":
                try:
                    if read_delivery(LAYOUT_FILE)["enabled"]:filename="custom.html"
                except (OSError,ValueError,TypeError):
                    pass  # Invalid custom layouts fall back to the standard cockpit.
            original = WEB_DIR / filename
            override = OVERRIDE_DIR / "app" / "web" / filename
            path = override if filename in allowed and override.is_file() else original
            self.send_bytes(path.read_bytes(), mime=mime, cache="no-store")
            return
        if url.path == "/api/headunit-prefs":
            try:
                return self.send_json(read_prefs(PREFS_FILE))
            except (OSError, ValueError, TypeError) as exc:
                return self.send_json({"error": str(exc)}, 503)
        if url.path == "/api/ui-config":
            try:
                return self.send_json(read_config(CONFIG_FILE))
            except (OSError, ValueError, TypeError) as exc:
                LOG.warning("Ignoring invalid UI configuration: %s", exc)
                return self.send_json({"error": "Invalid UI configuration"}, 503)
        if url.path == "/api/update-status":
            try:
                revision = subprocess.check_output(
                    ["git", "-C", str(WEB_DIR.parent.parent), "rev-parse", "--short", "HEAD"],
                    stderr=subprocess.DEVNULL, timeout=2).decode().strip()
            except (OSError, subprocess.SubprocessError):
                revision = "unknown"
            try:
                result = subprocess.run(["systemctl", "is-enabled", "car-dashboard-update.timer"],
                                        capture_output=True, text=True, timeout=2, check=False)
                timer = "GitHub update timer: " + result.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                timer = "GitHub update timer: unavailable"
            try:
                cfg = json.loads(Path(os.environ.get("CAR_UI_CONFIG", "/etc/car-dashboard/ui-config.json")).read_text())
                config_rev = int(cfg.get("revision", 0))
            except (OSError, ValueError, TypeError):
                config_rev = 0
            try:
                courier = json.loads(Path("/etc/car-dashboard/courier-last.json").read_text())
            except (OSError, ValueError):
                courier = None
            try:
                courier_progress=json.loads(Path('/etc/car-dashboard/courier-progress.json').read_text())
            except (OSError,ValueError):courier_progress=None
            try:
                fleet_status=json.loads(Path('/etc/car-dashboard/fleet-status.json').read_text())
            except (OSError,ValueError):fleet_status=None
            try:overlay_revision=json.loads((OVERRIDE_DIR/'active.json').read_text()).get('generation')
            except (OSError,ValueError):overlay_revision=None
            try:software_status=json.loads((Path.home()/'.local/state/drive-console/software/status.json').read_text())
            except (OSError,ValueError):software_status=None
            try:reboot_status=json.loads(Path('/etc/car-dashboard/reboot-status.json').read_text())
            except (OSError,ValueError):reboot_status=None
            try:radio_status=json.loads((Path.home()/'.local/state/drive-console/software/radio-status.json').read_text())
            except (OSError,ValueError):radio_status={"phase":"unconfigured"}
            try:radio_test=json.loads((Path.home()/'.local/state/drive-console/software/radio-test-result.json').read_text())
            except (OSError,ValueError):radio_test={"phase":"pending","message":"Waiting for configured radio and pinned application encryption keys."}
            return self.send_json({"revision": revision, "updateTimer": timer,
                                   "overlayRevision":overlay_revision,"softwareUpdates":software_status,"reboot":reboot_status,
                                   "fleet":fleet_status,
                                   "courierProgress":courier_progress,
                                   "courier": courier,
                                   "lorawan": "Local radio: "+radio_status.get("phase","unconfigured")+"; encrypted test: "+radio_test.get('phase','pending'),"radio":radio_status,"radioDiagnostic":radio_test,
                                   "configRevision": config_rev,
                                   "packagesExecutable": False})
        if url.path == "/api/gps":
            return self.send_json(query_gpsd())
        tile = local_tile_path(url.path)
        if tile is not None:
            try:
                if not tile.is_file():
                    match=TILE_ROUTE.fullmatch(url.path)
                    packed=mbtiles_tile(*map(int,match.groups()))
                    if packed:return self.send_bytes(packed[0],mime=packed[1],cache='public, max-age=86400')
                if not tile.is_file() or tile.stat().st_size > 2097152:
                    return self.send_json({"error": "Local map tile missing"}, 404)
                return self.send_bytes(tile.read_bytes(), mime="image/png", cache="public, max-age=86400")
            except (OSError,sqlite3.Error):
                return self.send_json({"error": "Local map tile unavailable"}, 404)
        if url.path.startswith("/tiles/"):
            return self.send_json({"error": "Invalid tile path"}, 404)
        if url.path == "/api/build":
            # Read installed Git revision, so an already-open kiosk reloads after a dashboard update.
            try:
                sha = subprocess.check_output(
                    ["git", "-C", str(WEB_DIR.parent.parent), "rev-parse", "HEAD"],
                    stderr=subprocess.DEVNULL, timeout=2
                ).decode("ascii").strip()
            except (OSError, subprocess.SubprocessError, UnicodeError):
                sha = "not-a-git-checkout"
            return self.send_json({"revision": sha})
        if url.path == "/api/health":
            return self.send_json({"ok": True, "databaseExists": DB_PATH.is_file(), "readOnly": True})
        if url.path not in ROUTES and url.path != "/api/history":
            return self.send_json({"error": "Not found"}, 404)
        try:
            db = connect()
            try:
                if not table_exists(db, "readings"):
                    raise ValueError("Database does not contain a readings table")
                data = route_history(db, parse_qs(url.query)) if url.path == "/api/history" else ROUTES[url.path](db)
                return self.send_json(data)
            finally:
                db.close()
        except (FileNotFoundError, sqlite3.Error, ValueError, TypeError) as exc:
            LOG.warning("Request %s failed: %s", url.path, exc)
            self.send_json({"error": str(exc)}, 503)

    def camera_request(self,url):
        from control import car_data
        try:
            config=json.loads(FLEET_FILE.read_text())
            home=config['home'];role=config['role'];query=parse_qs(url.query)
            if url.path=='/api/dashcam':return self.send_json(car_data.clips(home,role,query.get('after',[''])[0]))
            path=car_data.clip_path(home,role,query.get('id',[''])[0])
            size=path.stat().st_size;start=0;end=size-1
            requested=self.headers.get('Range')
            if requested:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
                if not match or not any(match.groups()):raise ValueError('Single byte range required')
                first,last=match.groups()
                if first:start=int(first);end=min(size-1,int(last)) if last else size-1
                else:start=max(0,size-int(last))
                if start>end or start>=size:
                    self.send_response(416);self.send_header('Content-Range','bytes */'+str(size));self.send_header('Content-Length','0');self.end_headers();return
            mime={'.mp4':'video/mp4','.mov':'video/quicktime','.mkv':'video/x-matroska','.ts':'video/mp2t'}[path.suffix]
            with path.open('rb') as stream:
                self.send_response(206 if requested else 200)
                self.send_header('Content-Type',mime);self.send_header('Content-Length',str(end-start+1))
                self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                if requested:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.end_headers();stream.seek(start);remaining=end-start+1
                while remaining:
                    chunk=stream.read(min(65536,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            if isinstance(exc,(BrokenPipeError,ConnectionResetError)):return
            self.send_json({'error':str(exc)},404)

    def report_request(self, url):
        db = gps = None
        sent = False
        try:
            query = parse_qs(url.query, keep_blank_values=True)
            kind = url.path[len("/api/report/"):]
            db = reporting.connect(DB_PATH)
            gps = reporting.connect(GPS_DB_PATH)
            if kind == "summary":
                return self.send_json(reporting.summary(db, gps))
            export = kind == "export"
            if export:kind = query.get("kind", ["values"])[0]
            target = gps if kind in ("gps", "stops", "gps-messages", "gps-events", "gps-stream", "gps-sessions") else db
            if not export:return self.send_json(reporting.page(target, kind, query))
            # Validate before sending headers, then stream every matching row.
            reporting.selection(kind, query)
            self.protocol_version = "HTTP/1.1"
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="'+kind+'.csv"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            sent = True
            self.close_connection = True
            for batch in reporting.csv_batches(target, kind, query):
                self.wfile.write((format(len(batch),"x")+"\r\n").encode()+batch+b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
            if not sent and not self.wfile.closed:
                self.send_json({"error": str(exc)}, 400)
        finally:
            if db is not None:db.close()
            if gps is not None:gps.close()

    def do_POST(self):
        endpoint=urlsplit(self.path).path
        if endpoint not in ("/api/headunit-prefs","/api/messages","/api/local-sync/file","/api/radio-test","/api/map-layers"):
            return self.send_json({"error": "Not found"}, 404)
        # Local kiosk settings only; never send commands to the vehicle.
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self.send_json({"error": "Localhost required"}, 403)
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            return self.send_json({"error": "JSON content type required"}, 415)
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + HOST + ":" + str(PORT):
            return self.send_json({"error": "Untrusted origin"}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > (32*1024**2 if endpoint=='/api/map-layers' else 131072 if endpoint=='/api/local-sync/file' else 12000):
                return self.send_json({"error": "Invalid request size"}, 413)
            incoming = json.loads(self.rfile.read(size))
            if endpoint=='/api/map-layers':
                from control.map_layers import location,action
                return self.send_json(action(location(),incoming))
            if endpoint=='/api/local-sync/file':
                from control.file_sync import car_files
                config=json.loads(FLEET_FILE.read_text());config.update(json.loads(Path('/etc/car-dashboard/update-link.json').read_text()))
                files=car_files(config)
                if incoming.get('action')=='save':return self.send_json(files.edit(incoming['name'],incoming['sha256'],incoming['source']))
                if incoming.get('action')=='resolve':return self.send_json(files.resolve(incoming['name'],incoming['id'],incoming['sha256'],incoming['choice'],incoming.get('source')))
                return self.send_json({'error':'Unknown selected-file action'},400)
            if endpoint=='/api/radio-test':
                from control.radio_diagnostic import Diagnostic
                config=json.loads(FLEET_FILE.read_text());home=Path(config['home']);key=home/'.config/car-dashboard/update-link.key'
                if set(incoming)!={'action'} or incoming['action'] not in ('start','restart'):raise ValueError('Choose start or restart for the encrypted diagnostic')
                options=json.loads(Path('/etc/car-dashboard/update-link.json').read_text())
                if config.get('role')!='tiny' or not options.get('radio_model') or not options.get('radio_device'):raise ValueError('Configure the Tiny radio adapter before starting a radio test')
                if not (home/'.config/car-dashboard/radio-home.pub').is_file():raise ValueError('Pinned home radio key required')
                diagnostic=Diagnostic(home/'.local/state/drive-console/software/radio-test-result.json',bytes.fromhex(key.read_text().strip()))
                if incoming.get('action')=='restart':diagnostic.restart()
                else:diagnostic.start()
                return self.send_json(diagnostic.read())
            if endpoint=='/api/messages':
                from control.messages import enqueue,local_events,cancel
                config=json.loads(FLEET_FILE.read_text())
                events=local_events(config)
                if incoming.get('action')=='cancel':return self.send_json(cancel(events,incoming.get('id')))
                return self.send_json(enqueue(events,config['role'],incoming.get('destination','home'),incoming.get('text'),incoming.get('action','text'),incoming.get('clientId')))
            saved = update_prefs(PREFS_FILE, incoming)
            return self.send_json(saved)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as exc:
            return self.send_json({"error": str(exc)}, 400)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/live"):
            LOG.info("%s %s", self.address_string(), fmt % args)


def mbtiles_tile(z,x,y,path=None):
    path=Path(path) if path is not None else MAP_MBTILES
    if not path.is_file() or path.is_symlink():return None
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as database:
        row=database.execute('SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=? AND length(tile_data)<=2097152 LIMIT 1',(z,x,(1<<z)-1-y)).fetchone()
    if not row:return None
    data=bytes(row[0])
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return data,'image/png'
    if data.startswith(b'\xff\xd8\xff'):return data,'image/jpeg'
    return None  # Vector PBF packs need a vector renderer, not a raster image tag.


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not HOST.startswith("127.") and HOST not in ("localhost", "::1"):
        LOG.warning("Dashboard has no login authentication! Do not expose it to the public internet.")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    LOG.info("Car dashboard http://%s:%s | DB=%s | read-only", HOST, PORT, DB_PATH)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
