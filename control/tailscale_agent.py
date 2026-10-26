"""Poll the home panel through a local SSH/Tailscale tunnel; settings only."""
import json
import logging
import os
import time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import URLError
from urllib.parse import urlsplit

from control.config_core import read_config, validate, write_config
from control.layouts import read_delivery, validate_delivery, write_delivery


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the device credential to another destination.
        return None


def sync_once(base_url, token, config_path, layout_path=None):
    url = urlsplit(base_url)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or
            url.username or url.password or url.path not in ("", "/") or
            url.query or url.fragment):
        raise ValueError("Use an HTTP SSH tunnel on 127.0.0.1")
    if len(token) < 32:
        raise ValueError("Device token must have at least 32 characters")
    # Local tunnel traffic must not send the credential through an HTTP proxy.
    client = build_opener(ProxyHandler({}), NoRedirect())

    def call(path, body=None):
        request = Request(base_url.rstrip("/")+path,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={"Authorization": "Bearer "+token,
                                   "Content-Type": "application/json"})
        with client.open(request, timeout=15) as response:
            raw = response.read(512001)
            if len(raw) > 512000:
                raise ValueError("Oversized response")
            return json.loads(raw)

    desired = validate(call("/api/device/config")["config"])
    current = read_config(config_path)
    if desired["revision"] < current["revision"]:
        raise ValueError("Home revision is behind car; reconcile before syncing")
    if desired["revision"] == current["revision"] and desired != current:
        raise ValueError("Conflicting configurations at the same revision")
    if desired != current:
        write_config(config_path, desired)
    # Read back before ACK. Lost responses/restarts safely repeat the same ACK.
    if read_config(config_path) != desired:
        raise ValueError("Configuration changed before acknowledgment")
    if call("/api/device/ack", {"revision": desired["revision"]}).get("ok") is not True:
        raise ValueError("Home did not accept acknowledgment")
    if layout_path is not None:
        layout = validate_delivery(call("/api/device/layout"))
        saved = read_delivery(layout_path)
        if layout["revision"] < saved["revision"]:
            raise ValueError("Home layout is behind car")
        if layout["revision"] == saved["revision"] and layout != saved:
            raise ValueError("Conflicting layout revision")
        if layout != saved:
            write_delivery(layout_path, layout)
        if read_delivery(layout_path) != layout:
            raise ValueError("Layout changed before acknowledgment")
        if call("/api/device/layout-ack", {"revision": layout["revision"]}).get("ok") is not True:
            raise ValueError("Layout acknowledgment failed")
    return desired["revision"]


def main():
    logging.basicConfig(level=logging.INFO)
    base = os.getenv("CAR_HOME_URL", "http://127.0.0.1:8791")
    token = Path(os.environ["CAR_DEVICE_TOKEN_FILE"]).read_text().strip()
    config = Path(os.getenv("CAR_UI_CONFIG", "/etc/car-dashboard/ui-config.json"))
    interval = int(os.getenv("CAR_SYNC_SECONDS", "15"))
    if not 2 <= interval <= 3600:
        raise SystemExit("CAR_SYNC_SECONDS must be between 2 and 3600")
    while True:
        try:
            revision = sync_once(base, token, config, Path(os.getenv("CAR_LAYOUT_FILE", str(config.with_name("layout.json")))))
            logging.info("Home configuration revision %s saved and acknowledged", revision)
        except (OSError, URLError, ValueError, TypeError, KeyError):
            # Avoid logging request objects, tokens or remote response bodies.
            logging.warning("Home sync failed; retaining local configuration and retrying")
        time.sleep(interval)


if __name__ == "__main__":
    main()
