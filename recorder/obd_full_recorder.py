#!/usr/bin/env python3
"""Read-only vehicle OBD2 collector, independent of the web dashboard.

Only standard read modes 01/02/03/06/07/09 are permitted; no VIN reads,
ECU writes, configuration, clear DTCs, actuator controls or custom requests.
"""
from __future__ import annotations
import argparse
import fcntl
import math
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import time

SAFE_WIRE = re.compile(r"^(?:01|02|03|06|07|09)(?:[0-9A-F]{2})?$")
EXCLUDED_NAMES = {"GET_VIN", "VIN"}
FAST = {"RPM": 2.5, "SPEED": 2.5}
MEDIUM = {"CONTROL_MODULE_VOLTAGE": 12, "ENGINE_LOAD": 12,
          "THROTTLE_POS": 12, "COOLANT_TEMP": 12}
SLOW = {"FUEL_LEVEL": 40, "INTAKE_TEMP": 40,
        "AMBIANT_AIR_TEMP": 40, "FUEL_RAIL_PRESSURE_DIRECT": 40,
        "SHORT_FUEL_TRIM_1": 20, "LONG_FUEL_TRIM_1": 40}
STOP = False

def timestamp():
    return int(time.time())

def wire_code(command):
    raw = getattr(command, "command", b"")
    if isinstance(raw, bytes):
        return raw.decode("ascii", errors="replace").upper().strip()
    return str(raw).upper().strip()

def approved(command):
    return (getattr(command, "name", "").upper() not in EXCLUDED_NAMES
            and wire_code(command) != "0902"
            and SAFE_WIRE.fullmatch(wire_code(command)) is not None)

def supported_commands(connection, obd_module):
    result = {}
    for table in obd_module.commands.modes:
        for cmd in table:
            if cmd is not None and approved(cmd) and connection.supports(cmd):
                result[(wire_code(cmd), cmd.name)] = cmd
    return [result[key] for key in sorted(result)]

def detail(value):
    text = str(value) if value is not None else None
    number = None
    unit = None
    if hasattr(value, "magnitude") and hasattr(value, "units"):
        unit = str(value.units)
        try:
            number = float(value.magnitude)
        except (TypeError, ValueError, OverflowError):
            pass
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    if number is not None and not math.isfinite(number):
        number = None
    return text, number, unit

def open_db(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=15)
    db.execute("PRAGMA busy_timeout = 15000")
    # Explicit per-connection durability; committed WAL survives process crashes.
    # Actual power-loss durability still depends on filesystem/drive flush support.
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = FULL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS readings (
            ts INTEGER NOT NULL, name TEXT NOT NULL, value TEXT,
            number REAL, unit TEXT);
        CREATE TABLE IF NOT EXISTS events (
            ts INTEGER NOT NULL, name TEXT NOT NULL, detail TEXT);
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY, stage TEXT, started INTEGER,
            total INTEGER, successes INTEGER);
        CREATE TABLE IF NOT EXISTS command_attempts (
            id INTEGER PRIMARY KEY, ts REAL NOT NULL, completed REAL NOT NULL,
            name TEXT NOT NULL, command TEXT NOT NULL, status TEXT NOT NULL,
            duration_ms REAL NOT NULL, value TEXT, number REAL, unit TEXT,
            error TEXT, raw TEXT, snapshot_id INTEGER);
        CREATE INDEX IF NOT EXISTS command_attempts_name_id ON command_attempts(name,id);
        CREATE TABLE IF NOT EXISTS recorder_snapshot_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id INTEGER,
            ts INTEGER, name TEXT, value TEXT, number REAL, unit TEXT);
    """)
    required = {
        "readings": {"ts", "name", "value", "number", "unit"},
        "events": {"ts", "name", "detail"},
        "snapshots": {"id", "stage", "started", "total", "successes"},
    }
    for table, cols in required.items():
        actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if not cols <= actual:
            db.close()
            raise RuntimeError(f"Existing {table} table is incompatible; missing: {cols - actual}")
    return db

def record_event(db, name, text):
    db.execute("INSERT INTO events(ts,name,detail) VALUES(?,?,?)", (timestamp(), name, str(text)))
    db.commit()
    print(f"{name}: {text}", flush=True)

def record_value(db, cmd, response, snapshot_id=None):
    if not approved(cmd) or response is None or response.is_null():
        return False
    label, number, unit = detail(response.value)
    ts = timestamp()
    db.execute("INSERT INTO readings(ts,name,value,number,unit) VALUES(?,?,?,?,?)",
               (ts, cmd.name, label, number, unit))
    if snapshot_id is not None:
        db.execute("""INSERT INTO recorder_snapshot_values
                   (snapshot_id,ts,name,value,number,unit) VALUES(?,?,?,?,?,?)""",
                   (snapshot_id, ts, cmd.name, label, number, unit))
    db.commit()
    return True

def safe_query(connection, cmd):
    if not approved(cmd):
        raise ValueError(f"Refusing non-read-only OBD command: {getattr(cmd, 'name', '?')}")
    return connection.query(cmd)

def collect_query(db, connection, cmd, snapshot_id=None):
    """Persist every request outcome, including empty replies and exceptions."""
    started = time.time()
    clock = time.monotonic()
    status, error, raw = "empty", None, []
    label = number = unit = None
    response = None
    try:
        response = safe_query(connection, cmd)
        if response is not None:
            for message in getattr(response, "messages", ()) or ():
                wire = getattr(message, "raw", None)
                try:
                    value = wire() if callable(wire) else str(message)
                    raw.append(value.hex() if isinstance(value, bytes) else str(value))
                except Exception as exc:
                    raw.append({"captureError":type(exc).__name__})
        if response is not None and not response.is_null():
            label, number, unit = detail(response.value)
            status = "ok"
    except Exception as exc:
        status = "error" if approved(cmd) else "rejected"
        error = f"{type(exc).__name__}: {exc}"
    db.execute("""INSERT INTO command_attempts
        (ts,completed,name,command,status,duration_ms,value,number,unit,error,raw,snapshot_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (started,time.time(),cmd.name,wire_code(cmd),status,
         round((time.monotonic()-clock)*1000,3),label,number,unit,error,json.dumps(raw),snapshot_id))
    if status == "ok":
        record_value(db, cmd, response, snapshot_id)
    else:
        db.commit()
    return status == "ok"


def snapshot(db, connection, commands, stage):
    started = timestamp()
    cur = db.execute("INSERT INTO snapshots(stage,started,total,successes) VALUES(?,?,?,?)",
                     (stage, started, len(commands), 0))
    db.commit()
    sid = cur.lastrowid
    record_event(db, "snapshot_start", f"{stage}: {len(commands)} safe supported commands")
    successes = 0
    for cmd in commands:
        if STOP:
            break
        try:
            if collect_query(db, connection, cmd, snapshot_id=sid):
                successes += 1
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Snapshot {cmd.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    db.execute("UPDATE snapshots SET successes=? WHERE id=?", (successes, sid))
    db.commit()
    record_event(db, "snapshot_end", f"{stage}: {successes}/{len(commands)} responses")

def run_loop(db, connection, commands, stop_check=lambda: STOP):
    by_name = {cmd.name: cmd for cmd in commands}
    rates = dict(FAST)
    rates.update(MEDIUM)
    rates.update(SLOW)
    schedule = {name: 0.0 for name in rates if name in by_name}
    record_event(db, "connected", str(connection.protocol_name()))
    connected_at = time.monotonic()
    snapshot(db, connection, commands, "connect")
    next_snapshot = connected_at + 300
    stage = "after_5_minutes"
    count = 0
    while not stop_check():
        now = time.monotonic()
        if now >= next_snapshot:
            snapshot(db, connection, commands, stage)
            next_snapshot += 3600
            stage = "hourly"
            continue
        if not connection.is_connected():
            return
        due = [name for name, ready in schedule.items() if ready <= now]
        if not due:
            time.sleep(min(0.25, max(0.0, min(schedule.values(), default=now + 0.25) - now)))
            continue
        for name in due:
            if stop_check():
                return
            try:
                collect_query(db, connection, by_name[name])
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"Poll {name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            schedule[name] = time.monotonic() + rates[name]
            count += 1
            if count % 30 == 0:
                print(f"Polled {count} sensor requests", flush=True)

def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only OBD2 SQLite recorder")
    parser.add_argument("--port", default=os.getenv("OBD_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--db", default=os.getenv("CAR_DB", str(Path.home() / "car-console/obd-data/vehicle.sqlite3")))
    parser.add_argument("--timeout", type=float, default=1.5)
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 10:
        parser.error("--timeout must be between 0 and 10 seconds")
    try:
        import obd
    except ImportError:
        parser.error("python-OBD is not installed; run deploy/install-recorder.sh")
    global STOP
    def shutdown(_sig, _frame):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    lock = Path(os.getenv("OBD_RECORDER_LOCK", "/tmp/car-obd-recorder.lock"))
    with lock.open("a+") as fd:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another managed recorder is running; refusing duplicate connection", file=sys.stderr)
            return 2
        db = open_db(args.db)
        try:
            record_event(db, "program_started", f"Read-only recorder on {args.port}")
            while not STOP:
                connection = None
                try:
                    if not Path(args.port).exists():
                        record_event(db, "ecu_offline", f"Adapter missing: {args.port}")
                    else:
                        connection = obd.OBD(portstr=args.port, fast=False,
                                             timeout=args.timeout, check_voltage=False)
                        if connection.is_connected():
                            commands = supported_commands(connection, obd)
                            if commands:
                                run_loop(db, connection, commands)
                            else:
                                record_event(db, "ecu_offline", "No approved supported commands")
                        else:
                            record_event(db, "ecu_offline", "No ECU response; waiting")
                except Exception as exc:
                    record_event(db, "recorder_error", f"{type(exc).__name__}: {exc}")
                finally:
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:
                            pass
                if not STOP:
                    for _ in range(60):
                        if STOP:
                            break
                        time.sleep(0.5)
        finally:
            record_event(db, "program_stopped", "shutdown")
            db.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
