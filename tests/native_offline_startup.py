"""Run in the pinned image on an internal Docker LAN and disposable /config."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, '/src')
from deploy.home_assistant import managed_files, STARTER_MIGRATION, FORK_ARCHIVE_SHA
from app.ha_bridge import snapshot
import aiohttp

assert os.getenv('CAR_DISPOSABLE_VALIDATION') == '1'
import hashlib
import importlib.util
payload=Path('/src/car_assistant/runtime.tar.gz').read_bytes()
assert hashlib.sha256(payload).hexdigest()==FORK_ARCHIVE_SHA
runtime=Path('/tmp/car-assistant-native-runtime');runtime.mkdir()
# Match Docker COPY overlay semantics, retaining the dependency base's generated
# backend translations and assets that are not part of the upstream Git source.
for module,destination in (
    ('homeassistant',runtime/'usr/src/homeassistant/homeassistant'),
    ('hass_frontend',runtime/'usr/local/lib/python3.14/site-packages/hass_frontend')):
    original=next(iter(importlib.util.find_spec(module).submodule_search_locations))
    shutil.copytree(original,destination)
import io
with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
    archive.extractall(runtime,filter='data')
pythonpath=str(runtime/'usr/src/homeassistant')+':'+str(runtime/'usr/local/lib/python3.14/site-packages')
for path in pythonpath.split(':'):sys.path.insert(0,path)
root = Path('/config')
assert not (root / 'configuration.yaml').exists(), 'Fresh disposable config required'
for name, raw in managed_files('/src', 'UTC').items():
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
exec(STARTER_MIGRATION)

async def removed_setup_resolves_waiters():
    from homeassistant.core import HomeAssistant
    from homeassistant.setup import async_set_domains_to_be_loaded, async_setup_component, async_wait_component
    hass=HomeAssistant('/config')
    async_set_domains_to_be_loaded(hass, {'script'})
    pending=asyncio.create_task(async_wait_component(hass,'script'))
    assert await async_setup_component(hass,'script',{}) is False
    assert await asyncio.wait_for(pending,1) is False

asyncio.run(removed_setup_resolves_waiters())

class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        raw = json.dumps(snapshot({'RPM': {'number': 1350, 'ts': time.time(),
            'unit': 'revolutions_per_minute'}}, {'fix': False})).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 8787), Backend)
threading.Thread(target=server.serve_forever, daemon=True).start()
client = build_opener(ProxyHandler({}))
base = 'http://127.0.0.1:8123'
def call(path, data=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with client.open(Request(base + path, json.dumps(data).encode() if data is not None else None,
                             headers), timeout=3) as response:
        return json.load(response)

started = time.monotonic()
with (root / 'startup.log').open('w') as log:
    env = dict(os.environ, PYTHONPATH=pythonpath)
    process = subprocess.Popen([sys.executable, '-m', 'homeassistant', '--config', '/config'], env=env,
                               stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = started + 120
        while True:
            try:
                call('/manifest.json')
                break
            except OSError:
                assert process.poll() is None, 'HA exited; see startup.log'
                assert time.monotonic() < deadline, 'Frontend not ready within 120s'
                time.sleep(.5)
        elapsed = time.monotonic() - started
        # The frontend can answer before the recorder and account dependencies
        # finish setup. Avoid racing owner creation against initial bootstrap.
        while 'Home Assistant initialized in' not in (root / 'startup.log').read_text():
            assert process.poll() is None
            assert time.monotonic() < deadline, 'Bootstrap not complete; see startup.log'
            time.sleep(.5)
        created = call('/api/onboarding/users', {'client_id': base + '/', 'name': 'Validation',
            'username': 'validation', 'password': secrets.token_urlsafe(32), 'language': 'en'})
        from urllib.parse import urlencode
        request = Request(base + '/auth/token', urlencode({'client_id': base + '/',
            'code': created['auth_code'], 'grant_type': 'authorization_code'}).encode())
        with client.open(request, timeout=5) as response:
            token = json.load(response)['access_token']
        while True:
            components = set(call('/api/config', token=token)['components'])
            assert not {'homeassistant_alerts', 'cloud', 'automation', 'script', 'conversation', 'assist_pipeline'} & components
            states = {row['entity_id']: row for row in call('/api/states', token=token)}
            if (states.get('sensor.car_rpm', {}).get('state') in ('1350','1350.0') and
                    {'frontend','config','history','logbook','car_console'} <= components):
                break
            assert time.monotonic() < deadline, 'Car bridge/local integrations not ready: ' + str(components) + str(states.get('sensor.car_rpm'))
            time.sleep(1)

        async def edit():
            async with aiohttp.ClientSession(trust_env=False) as session:
                async with session.ws_connect(base + '/api/websocket') as ws:
                    await ws.receive_json()
                    await ws.send_json({'type': 'auth', 'access_token': token})
                    assert (await ws.receive_json())['type'] == 'auth_ok'
                    await ws.send_json({'id': 1, 'type': 'lovelace/config', 'url_path': 'car-starter'})
                    reply = await ws.receive_json()
                    assert reply['success'], reply
                    layout = reply['result']
                    layout['views'][0]['title'] = 'Offline editable drive'
                    await ws.send_json({'id': 2, 'type': 'lovelace/config/save',
                        'url_path': 'car-starter', 'config': layout})
                    assert (await ws.receive_json())['success']
                    await ws.send_json({'id': 3, 'type': 'config/area_registry/list'})
                    reply = await ws.receive_json()
                    assert reply['success'] and reply['result'] == [], reply
                    await ws.send_json({'id': 4, 'type': 'config/area_registry/create', 'name': 'Unused room'})
                    reply = await ws.receive_json()
                    assert not reply['success'] and reply['error']['code']=='unknown_command', reply
        asyncio.run(edit())
        print(f'PASS: offline frontend ready in {elapsed:.1f}s; native editor save, authenticated car RPM; no automation/script/cloud/voice/alerts runtime')
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        server.shutdown()
