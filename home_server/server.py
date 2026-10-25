"""Local-only administrative API for the future LoRaWAN home gateway."""
import hmac
import json
import os
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from home_server.coordinator import Coordinator, Conflict

def main():
    token_file = os.environ.get("CAR_HOME_TOKEN_FILE")
    if not token_file:
        raise SystemExit("Set CAR_HOME_TOKEN_FILE to a private token file")
    path = Path(token_file).expanduser()
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit("Token file must be chmod 600")
    token = path.read_text().strip()
    if len(token) < 32:
        raise SystemExit("Token must have 32+ characters")
    store = Coordinator(os.environ.get("CAR_HOME_DB", str(Path.home()/".local/state/car-home/queue.sqlite3")))
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data):
            body = json.dumps(data, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def auth(self):
            return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)
        def do_GET(self):
            uri = urlsplit(self.path)
            if uri.path == "/health":
                return self.reply(200, {"ok": True, "lorawanConnected": False})
            if not self.auth():
                return self.reply(401, {"error": "Unauthorized"})
            if uri.path not in ("/api/state", "/api/pending"):
                return self.reply(404, {"error": "Not found"})
            try:
                device = parse_qs(uri.query)["device"][0]
                if uri.path == "/api/state":
                    return self.reply(200, store.state(device))
                return self.reply(200, store.pending(device))
            except (ValueError, KeyError, IndexError) as err:
                return self.reply(400, {"error": str(err)})
        def do_POST(self):
            if not self.auth():
                return self.reply(401, {"error": "Unauthorized"})
            if urlsplit(self.path).path != "/api/change":
                return self.reply(404, {"error": "Not found"})
            if self.headers.get("Origin") is not None:
                return self.reply(403, {"error": "Browser-origin POST disabled"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self.reply(415, {"error": "JSON required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 12000:
                    return self.reply(413, {"error": "Invalid request size"})
                data = json.loads(self.rfile.read(length))
                if type(data) is not dict or set(data) != {"device", "expectedRevision", "changes"}:
                    raise ValueError("Unexpected request structure")
                result = store.change(data["device"], data["expectedRevision"], data["changes"])
                return self.reply(200, result)
            except Conflict as err:
                return self.reply(409, {"error": str(err)})
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as err:
                return self.reply(400, {"error": str(err)})
    port = int(os.environ.get("CAR_HOME_PORT", "8790"))
    if not 1 <= port <= 65535:
        raise SystemExit("Invalid port")
    print(f"Home queue on 127.0.0.1:{port}; LoRaWAN bridge not configured", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

if __name__ == "__main__":
    main()
