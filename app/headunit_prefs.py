"""Local on-device vehicle preferences and scheduled destinations; no ECU writes."""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

DEFAULT = {
    "revision": 0,
    "vehicle": {"nickname": "", "model": "", "year": 2016},
    "display": {"theme": None, "units": None, "clockMode": "12h"},
    "destinations": [],
    "routines": [],
}
MAX_BYTES = 12000
IDENT = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
HHMM = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")

def _label(value, length=40, empty=False):
    if type(value) is not str or len(value) > length or (not empty and not value.strip()):
        raise ValueError("Invalid label")
    if any(ord(c) < 32 or c in "<>" for c in value):
        raise ValueError("Invalid label characters")
    return value.strip()

def _id(value):
    if type(value) is not str or IDENT.fullmatch(value) is None:
        raise ValueError("Invalid ID")
    return value

def validate(document):
    if type(document) is not dict or set(document) != set(DEFAULT):
        raise ValueError("Unexpected settings structure")
    revision = document["revision"]
    if type(revision) is not int or revision < 0:
        raise ValueError("Invalid settings revision")
    vehicle = document["vehicle"]
    if type(vehicle) is not dict or set(vehicle) != set(DEFAULT["vehicle"]):
        raise ValueError("Invalid vehicle profile")
    _label(vehicle["nickname"], 40, True)
    _label(vehicle["model"], 40, True)
    if type(vehicle["year"]) is not int or not 1980 <= vehicle["year"] <= 2100:
        raise ValueError("Invalid vehicle year")
    display = document["display"]
    if type(display) is not dict or set(display) != set(DEFAULT["display"]):
        raise ValueError("Invalid display preferences")
    if display["theme"] not in (None, "day", "night") or display["units"] not in (None, "us", "metric"):
        raise ValueError("Invalid display choices")
    if display["clockMode"] not in ("12h", "24h"):
        raise ValueError("Invalid clock mode")
    destinations = document["destinations"]
    routines = document["routines"]
    if type(destinations) is not list or len(destinations) > 12 or type(routines) is not list or len(routines) > 12:
        raise ValueError("Too many saved places or routines")
    destinations_by_id = set()
    for item in destinations:
        if type(item) is not dict or set(item) != {"id", "name", "lat", "lon", "usualMinutes"}:
            raise ValueError("Invalid destination")
        ident = _id(item["id"])
        if ident in destinations_by_id:
            raise ValueError("Duplicate destination")
        destinations_by_id.add(ident)
        _label(item["name"])
        for coord, bound in (("lat", 90), ("lon", 180)):
            val = item[coord]
            if type(val) not in (int, float) or not math.isfinite(val) or not -bound <= val <= bound:
                raise ValueError("Invalid GPS coordinate")
        minutes = item["usualMinutes"]
        if minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 720):
            raise ValueError("Invalid usual travel duration")
    routine_ids = set()
    for item in routines:
        if type(item) is not dict or set(item) != {"id", "name", "destinationId", "originId", "time", "days"}:
            raise ValueError("Invalid routine")
        ident = _id(item["id"])
        if ident in routine_ids:
            raise ValueError("Duplicate routine")
        routine_ids.add(ident)
        _label(item["name"])
        if _id(item["destinationId"]) not in destinations_by_id:
            raise ValueError("Missing routine destination")
        if item["originId"] is not None and _id(item["originId"]) not in destinations_by_id:
            raise ValueError("Missing routine origin")
        if type(item["time"]) is not str or HHMM.fullmatch(item["time"]) is None:
            raise ValueError("Invalid scheduled departure")
        days = item["days"]
        if type(days) is not list or not days or len(days) > 7 or len(set(map(str, days))) != len(days) or any(
            type(day) is not int or not 0 <= day <= 6 for day in days
        ):
            raise ValueError("Invalid routine weekdays")
    if len(json.dumps(document).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Settings too large")
    return json.loads(json.dumps(document))

def read(path):
    path = Path(path)
    if not path.exists():
        return json.loads(json.dumps(DEFAULT))
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Settings file exceeds size limit")
    return validate(json.loads(path.read_text(encoding="utf-8")))

def update(path, proposed):
    previous = read(path)
    valid = validate(proposed)
    if valid["revision"] != previous["revision"] + 1:
        raise ValueError("Settings changed elsewhere; refresh and retry")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".prefs-", delete=False) as stream:
            temp_path = stream.name
            os.fchmod(stream.fileno(), 0o600)
            json.dump(valid, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    return valid
