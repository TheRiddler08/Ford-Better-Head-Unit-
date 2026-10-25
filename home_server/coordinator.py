"""Durable home-side queue for safe, leaf-field head-unit changes.

No LoRaWAN transport or executable update mechanism is enabled by this module.
"""
import json
import sqlite3
import time
from pathlib import Path
from control.config_core import DEFAULT, apply_changes, validate


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Conflict(ValueError):
    pass


class Coordinator:
    def __init__(self, filename):
        self.filename = Path(filename)
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, config TEXT NOT NULL, ack INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS outbox (device TEXT NOT NULL, revision INTEGER NOT NULL, base INTEGER NOT NULL, changes TEXT NOT NULL, created INTEGER NOT NULL, PRIMARY KEY(device,revision))")
            db.execute("INSERT OR IGNORE INTO devices(id,config) VALUES (?,?)", ("car", compact(DEFAULT)))

    def connect(self):
        return sqlite3.connect(self.filename, timeout=10)

    def state(self, device="car"):
        with self.connect() as db:
            row = db.execute("SELECT config,ack FROM devices WHERE id=?", (device,)).fetchone()
            if not row:
                raise ValueError("Unknown device")
            return {"device": device, "config": validate(json.loads(row[0])), "acknowledgedRevision": row[1]}

    def change(self, device, expected, updates):
        if type(expected) is not int or expected < 0 or type(updates) is not dict or not updates:
            raise ValueError("Invalid request")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT config FROM devices WHERE id=?", (device,)).fetchone()
            if row is None:
                raise ValueError("Unknown device")
            current = validate(json.loads(row[0]))
            if current["revision"] != expected:
                raise Conflict("Stale revision; reload before editing")
            new = apply_changes(current, expected + 1, updates)
            db.execute("UPDATE devices SET config=? WHERE id=?", (compact(new), device))
            db.execute("INSERT INTO outbox(device,revision,base,changes,created) VALUES (?,?,?,?,?)",
                       (device, new["revision"], expected, compact(updates), int(time.time())))
        return {"device": device, "revision": expected + 1}

    def pending(self, device="car"):
        with self.connect() as db:
            row = db.execute("SELECT ack,config FROM devices WHERE id=?", (device,)).fetchone()
            if row is None:
                raise ValueError("Unknown device")
            ack, current = row
            pending = db.execute("SELECT base,revision,changes FROM outbox WHERE device=? AND revision>? ORDER BY revision LIMIT 129",
                                 (device, ack)).fetchall()
        if len(pending) > 128:
            return {"device": device, "snapshotRequired": True,
                    "latestRevision": json.loads(current)["revision"], "messages": []}
        return {"device": device, "acknowledgedRevision": ack, "snapshotRequired": False,
                "messages": [{"baseRevision": base, "revision": revision, "changes": json.loads(delta)}
                             for base, revision, delta in pending]}

    def record_authenticated_ack(self, device, revision):
        """Only an independently authenticated radio bridge may call this."""
        if type(revision) is not int:
            raise ValueError("Invalid ACK")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT ack,config FROM devices WHERE id=?", (device,)).fetchone()
            if row is None:
                raise ValueError("Unknown device")
            ack, current = row
            latest = validate(json.loads(current))["revision"]
            if revision < ack or revision > latest:
                raise ValueError("Invalid ACK sequence")
            db.execute("UPDATE devices SET ack=? WHERE id=?", (revision, device))
            db.execute("DELETE FROM outbox WHERE device=? AND revision<=?", (device, revision))
