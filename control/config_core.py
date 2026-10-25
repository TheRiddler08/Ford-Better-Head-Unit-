"""Restricted, versioned head-unit configuration shared by the car and home server."""
import copy
import json
from pathlib import Path

DEFAULT = {
  "schema": 1, "revision": 0,
  "theme": "night", "units": "us",
  "home": {
    "clock": {"visible": True, "order": 10},
    "media": {"visible": True, "order": 20},
    "map": {"visible": True, "order": 30},
    "vehicleArt": {"visible": True, "order": 40},
    "gauges": {"visible": True, "order": 50},
    "metrics": {"visible": True, "order": 60}
  },
  "nav": {"home": True, "navigation": True, "media": True,
          "vehicle": True, "diagnostics": True, "settings": True},
  "labels": {"vehicle": "MY VEHICLE", "dashboard": "DRIVE CONSOLE"},
  "telemetry": {"pollSeconds": 3},
}
ALLOWED_THEMES = {"night", "day"}
HOMES = set(DEFAULT["home"])
NAVS = set(DEFAULT["nav"])
LABELS = set(DEFAULT["labels"])
MAX_DOCUMENT = 8192

def _bool(v):
    if type(v) is not bool:
        raise ValueError("Expected true/false")
    return v

def validate(candidate):
    """Never accept arbitrary DOM/script, executable code, or system commands."""
    if not isinstance(candidate, dict) or len(json.dumps(candidate)) > MAX_DOCUMENT:
        raise ValueError("Invalid or oversized configuration")
    if set(candidate) != set(DEFAULT):
        raise ValueError("Unexpected configuration keys")
    if candidate["schema"] != 1 or type(candidate["revision"]) is not int or candidate["revision"] < 0:
        raise ValueError("Unsupported schema or revision")
    if candidate["theme"] not in ALLOWED_THEMES or candidate["units"] not in ("us", "metric"):
        raise ValueError("Invalid appearance settings")
    if set(candidate["home"]) != HOMES or set(candidate["nav"]) != NAVS or set(candidate["labels"]) != LABELS:
        raise ValueError("Invalid component keys")
    for item in candidate["home"].values():
        if set(item) != {"visible", "order"}:
            raise ValueError("Invalid component fields")
        _bool(item["visible"])
        if type(item["order"]) is not int or not 0 <= item["order"] <= 999:
            raise ValueError("Invalid component order")
    for value in candidate["nav"].values():
        _bool(value)
    if not candidate["nav"]["settings"] or not candidate["nav"]["home"]:
        raise ValueError("Home and settings must remain accessible")
    for label in candidate["labels"].values():
        if not isinstance(label, str) or len(label) > 40 or any(ord(c) < 32 for c in label):
            raise ValueError("Invalid text label")
    if set(candidate["telemetry"]) != {"pollSeconds"} or type(candidate["telemetry"]["pollSeconds"]) is not int:
        raise ValueError("Invalid telemetry options")
    if not 2 <= candidate["telemetry"]["pollSeconds"] <= 60:
        raise ValueError("Poll interval outside safe range")
    return copy.deepcopy(candidate)

def apply_changes(current, revision, changes):
    """Validate atomic changes before commit; reject stale/replayed revisions."""
    validate(current)
    if type(revision) is not int or revision <= current["revision"]:
        raise ValueError("Non-increasing configuration revision")
    if not isinstance(changes, dict) or len(changes) > 40:
        raise ValueError("Invalid change set")
    result = copy.deepcopy(current)
    for path, val in changes.items():
        parts = path.split(".")
        if len(parts) not in (1, 2, 3) or any(p.startswith("_") for p in parts):
            raise ValueError("Invalid configuration path")
        target = result
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                raise ValueError("Unknown configuration path")
            target = target[part]
        if parts[-1] not in target or parts[-1] in ("revision", "schema"):
            raise ValueError("Unknown or read-only configuration field")
        if isinstance(target[parts[-1]], dict):
            raise ValueError("Change leaf properties, not entire sections")
        target[parts[-1]] = val
    result["revision"] = revision
    return validate(result)

def diff(previous, next_config):
    validate(previous)
    validate(next_config)
    changes = {}
    def walk(prefix, old, new):
        for key, val in new.items():
            path = prefix + key
            if isinstance(val, dict):
                walk(path+".", old[key], val)
            elif key not in ("revision", "schema") and old[key] != val:
                changes[path] = val
    walk("", previous, next_config)
    return changes

def read_config(path):
    path=Path(path)
    if not path.is_file():
        return copy.deepcopy(DEFAULT)
    return validate(json.loads(path.read_text(encoding="utf-8")))

def write_config(path, config):
    """Atomically replace app config, never execute arbitrary code."""
    from os import replace, fsync, open as os_open, close, O_RDONLY
    from tempfile import NamedTemporaryFile
    valid=validate(config)
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,prefix=".config-",delete=False) as f:
        json.dump(valid,f,separators=(",",":"),sort_keys=True)
        f.write("\n")
        f.flush()
        fsync(f.fileno())
        temp=f.name
    replace(temp,path)
    directory = os_open(path.parent, O_RDONLY)
    try:
        fsync(directory)
    finally:
        close(directory)
