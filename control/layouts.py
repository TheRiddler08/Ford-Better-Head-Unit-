"""Versioned UI projects. User code is data and is never executed by Python."""
import copy
import json
import sqlite3
import time
from pathlib import Path

MAX_BYTES = 400_000


def project_default():
    return {"name": "My cockpit", "mode": "visual", "width": 1280, "height": 720,
            "background": "#081321", "pages": [{"id": "home", "name": "Home", "blocks": []}],
            "html": "<h1>My cockpit</h1>", "css": "", "js": ""}


def validate_project(value):
    if not isinstance(value, dict) or len(json.dumps(value).encode()) > MAX_BYTES:
        raise ValueError("Project exceeds 400 KB or is not an object")
    if set(value) != set(project_default()):
        raise ValueError("Unexpected project fields")
    if not isinstance(value["name"], str) or not 1 <= len(value["name"]) <= 80:
        raise ValueError("Project needs a name (up to 80 characters)")
    if value["mode"] not in ("visual", "code"):
        raise ValueError("Unknown editor mode")
    for field in ("width", "height"):
        if type(value[field]) is not int or not 240 <= value[field] <= 3840:
            raise ValueError("Canvas dimensions must be 240–3840 pixels")
    for field in ("html", "css", "js", "background"):
        if not isinstance(value[field], str):
            raise ValueError("Source fields must be text")
    if len(value["background"]) > 80:
        raise ValueError("Invalid background")
    pages = value["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= 12:
        raise ValueError("Use 1–12 pages")
    ids = set()
    for page in pages:
        if not isinstance(page, dict) or set(page) != {"id", "name", "blocks"}:
            raise ValueError("Invalid page")
        if not isinstance(page["id"], str) or not page["id"].isalnum() or len(page["id"]) > 60 or page["id"] in ids:
            raise ValueError("Page IDs must be unique letters/numbers")
        ids.add(page["id"])
        if not isinstance(page["name"], str) or not 1 <= len(page["name"]) <= 60:
            raise ValueError("Invalid page name")
        if not isinstance(page["blocks"], list) or len(page["blocks"]) > 80:
            raise ValueError("Use at most 80 components per page")
        block_ids = set()
        for block in page["blocks"]:
            fields = {"id", "type", "text", "x", "y", "w", "h", "color", "background", "fontSize", "sensor", "src", "target"}
            if not isinstance(block, dict) or set(block) != fields:
                raise ValueError("Invalid component fields")
            if not isinstance(block["id"], str) or not block["id"].isalnum() or block["id"] in block_ids:
                raise ValueError("Duplicate or invalid component ID")
            block_ids.add(block["id"])
            if block["type"] not in ("text", "heading", "button", "sensor", "clock", "image", "html", "panel"):
                raise ValueError("Unknown component")
            for field in ("text", "color", "background", "sensor", "src", "target"):
                if not isinstance(block[field], str):
                    raise ValueError("Component values must be text")
            for field in ("x", "y", "w", "h", "fontSize"):
                if type(block[field]) is not int or not 0 <= block[field] <= 7680:
                    raise ValueError("Invalid component geometry")
            if block["w"] < 20 or block["h"] < 20 or not 8 <= block["fontSize"] <= 200:
                raise ValueError("Invalid component size")
    return copy.deepcopy(value)


def validate_delivery(value):
    if not isinstance(value, dict) or set(value) != {"revision", "enabled", "project"}:
        raise ValueError("Invalid layout delivery")
    if type(value["revision"]) is not int or value["revision"] < 0 or type(value["enabled"]) is not bool:
        raise ValueError("Invalid layout revision")
    validate_project(value["project"])
    return copy.deepcopy(value)


def read_delivery(path):
    path = Path(path)
    if not path.exists():
        return {"revision": 0, "enabled": False, "project": project_default()}
    return validate_delivery(json.loads(path.read_text()))


def write_delivery(path, value):
    import os
    import tempfile
    value = validate_delivery(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
            temp = f.name
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


class LayoutStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS layouts (device TEXT PRIMARY KEY, state TEXT NOT NULL)")
        return db

    def initial(self):
        return {"generation": 0, "draft": project_default(), "published": {"revision": 0, "enabled": False, "project": project_default()},
                "acknowledgedRevision": 0, "history": []}

    def get(self, device):
        with self.connect() as db:
            row = db.execute("SELECT state FROM layouts WHERE device=?", (device,)).fetchone()
        return json.loads(row[0]) if row else self.initial()

    def update(self, device, generation, action, project=None, version=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM layouts WHERE device=?", (device,)).fetchone()
            state = json.loads(row[0]) if row else self.initial()
            if type(generation) is not int or generation != state["generation"]:
                raise ValueError("Layout changed elsewhere; reload before saving")
            if action in ("save", "publish"):
                state["draft"] = validate_project(project)
            if action in ("publish", "disable", "restore"):
                selected = state["draft"]
                enabled = action != "disable"
                if action == "restore":
                    matches = [item for item in state["history"] if item["revision"] == version]
                    if not matches:
                        raise ValueError("Version no longer available")
                    selected = matches[0]["project"]
                    enabled = matches[0]["enabled"]
                    state["draft"] = copy.deepcopy(selected)
                published = {"revision": state["published"]["revision"] + 1,
                             "enabled": enabled, "project": copy.deepcopy(selected)}
                state["published"] = published
                state["history"] = ([dict(published, created=int(time.time()))] + state["history"])[:20]
            elif action != "save":
                raise ValueError("Unknown layout action")
            state["generation"] += 1
            db.execute("INSERT OR REPLACE INTO layouts VALUES (?,?)", (device, json.dumps(state)))
        return state

    def ack(self, device, revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM layouts WHERE device=?", (device,)).fetchone()
            state = json.loads(row[0]) if row else self.initial()
            if type(revision) is not int or not state["acknowledgedRevision"] <= revision <= state["published"]["revision"]:
                raise ValueError("Invalid layout acknowledgment")
            state["acknowledgedRevision"] = revision
            db.execute("INSERT OR REPLACE INTO layouts VALUES (?,?)", (device, json.dumps(state)))


# This frame is navigated separately so custom inline JS does not require relaxing
# the authenticated panel's CSP. It receives only project HTML, never credentials.
FRAME_POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; "
                "object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'")
FRAME_HOST = b'''<!doctype html><html><head><meta charset="utf-8"></head><body><script>
addEventListener('message', function load(event) {
  if (event.source !== parent || event.data?.channel !== 'car-layout-host' || typeof event.data.html !== 'string') return;
  document.open(); document.write(event.data.html); document.close();
});
parent.postMessage({channel:'car-layout',type:'host-ready'}, '*');
</script></body></html>'''
