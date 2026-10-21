#!/usr/bin/env python3
"""Read-only vehicle telemetry dashboard. Python standard library only."""
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

WEB_DIR = Path(__file__).resolve().parent / "web"
DB_PATH = Path(os.environ.get("CAR_DB", str(Path.home() / "car-console/obd-data/vehicle.sqlite3"))).expanduser()
HOST = os.environ.get("CAR_BIND", "127.0.0.1")
PORT = int(os.environ.get("CAR_PORT", "8787"))
TIME_RANGES = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}
LOG = logging.getLogger("car-dashboard")


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


ROUTES = {"/api/live": route_live, "/api/summary": route_summary,
          "/api/sensors": route_sensors, "/api/snapshots": route_snapshots,
          "/api/events": route_events, "/api/trips": route_trips,
          "/api/alerts": route_alerts}
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}


class Handler(BaseHTTPRequestHandler):
    server_version = "CarDashboard/1.0"

    def send_bytes(self, payload, status=200, mime="application/json; charset=utf-8", cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, allow_nan=False, default=str).encode(), status)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path in STATIC:
            filename, mime = STATIC[url.path]
            self.send_bytes((WEB_DIR / filename).read_bytes(), mime=mime, cache="public, max-age=120")
            return
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

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/live"):
            LOG.info("%s %s", self.address_string(), fmt % args)


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
