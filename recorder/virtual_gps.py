#!/usr/bin/env python3
"""Independent NMEA serial GPS source. No dashboard/recorder simulation API."""
import argparse
import fcntl
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import math
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading
from urllib.parse import urlsplit
import os
from pathlib import Path
import pty
import pwd
import re
import stat
import tempfile
import select
import shutil
import signal
import socket
import subprocess
import time
import tty


def sentence(body):
    checksum = 0
    for byte in body.encode('ascii'):
        checksum ^= byte
    return f'${body}*{checksum:02X}\r\n'.encode('ascii')


def coordinate(value, latitude):
    degrees = int(abs(value))
    minutes = (abs(value) - degrees) * 60
    # Rounding must carry into degrees near a degree boundary.
    minutes = round(minutes, 5)
    if minutes >= 60:
        degrees += 1
        minutes = 0
    return f'{degrees:0{2 if latitude else 3}d}{minutes:08.5f}', ('N' if value >= 0 else 'S') if latitude else ('E' if value >= 0 else 'W')


def frame(lat, lon, speed, course, stamp=None, altitude=100.0):
    stamp = stamp or datetime.now(timezone.utc)
    a, ns = coordinate(lat, True)
    b, ew = coordinate(lon, False)
    clock = stamp.strftime('%H%M%S') + f'.{stamp.microsecond//10000:02d}'
    knots = '' if speed is None else f'{speed/0.514444:.2f}'
    heading = '' if course is None else f'{course:.2f}'
    height = '' if altitude is None else f'{altitude:.1f}'
    return b''.join([
        sentence(f'GPRMC,{clock},A,{a},{ns},{b},{ew},{knots},{heading},{stamp:%d%m%y},,,A'),
        sentence(f'GPGGA,{clock},{a},{ns},{b},{ew},1,08,0.9,{height},M,0.0,M,,'),
        sentence(f'GPGSA,A,{3 if altitude is not None else 2},01,02,03,04,05,06,07,08,,,,,1.5,0.9,1.2'),
    ])


def position(lat, lon, radius, speed, elapsed):
    angle = elapsed * speed / radius
    north, east = radius * math.sin(angle), radius * math.cos(angle)
    return lat + north/111320, lon + east/(111320*math.cos(math.radians(lat))), math.degrees(-angle) % 360


BROWSER_PAGE = b'''<!doctype html><meta charset="utf-8"><title>Laptop GPS source</title>
<h1>Laptop location to virtual GPS</h1>
<p>This computer's browser provides location. Positions enter normal car history.</p>
<button id="start">Share this laptop's location</button><button id="stop">Stop sharing</button>
<p id="status">Not sharing. Keep this page open while testing.</p>
<script>
const token=new URLSearchParams(location.hash.slice(1)).get('token');
let watch=null,latest=null,busy=false;
const status=document.getElementById('status');
async function send(){if(busy||!latest)return;if(Date.now()-latest.timestamp>25000){latest=null;status.textContent='Location paused: waiting for a fresh fix. Return to this window or use the native location helper.';return;}busy=true;try{
 const r=await fetch('/fix',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify(latest)});
 if(r.status===202){latest=null;status.textContent='Waiting for a fresh location fix; background location may be paused.';return;}
 if(!r.ok)throw Error('Source rejected location: HTTP '+r.status);
 status.textContent='Sharing: '+latest.latitude+', '+latest.longitude+'; accuracy +/-'+Math.round(latest.accuracy)+' m';
}catch(e){status.textContent=e.message;}finally{busy=false;}}
function stop(){if(watch!==null)navigator.geolocation.clearWatch(watch);watch=null;latest=null;fetch('/fix',{method:'DELETE',headers:{Authorization:'Bearer '+token}}).catch(()=>{});status.textContent='Stopped';}
function located(p){const c=p.coords;latest={latitude:c.latitude,longitude:c.longitude,accuracy:c.accuracy,altitude:c.altitude,speed:c.speed,heading:c.heading,timestamp:p.timestamp};send();}
document.getElementById('start').onclick=()=>{
 if(watch!==null)return;
 if(!navigator.geolocation){status.textContent='Location unavailable: use localhost or HTTPS.';return;}
 status.textContent='Waiting for location permission / fix';
 watch=navigator.geolocation.watchPosition(located,e=>{latest=null;status.textContent=e.message;},{enableHighAccuracy:true,maximumAge:0,timeout:15000});
};
document.getElementById('stop').onclick=stop;
setInterval(send,1000);
function refresh(){if(watch!==null)navigator.geolocation.getCurrentPosition(located,e=>{status.textContent=e.message;},{enableHighAccuracy:true,maximumAge:0,timeout:15000});}
setInterval(refresh,10000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
window.addEventListener('focus',refresh);
</script>'''


class BrowserSource:
    def __init__(self, port):
        self.token=secrets.token_urlsafe(32)
        self.fix=None
        self.lock=threading.Lock()
        source=self
        class Handler(BaseHTTPRequestHandler):
            def reply(self, code, body=b''):
                self.send_response(code)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers();self.wfile.write(body)
            def authorized(self):
                origin=self.headers.get('Origin')
                allowed=(f'http://127.0.0.1:{source.server.server_port}',f'http://localhost:{source.server.server_port}')
                return (not origin or origin in allowed) and secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+source.token)
            def do_GET(self):
                self.reply(200,BROWSER_PAGE) if self.path=='/' else self.reply(404)
            def do_POST(self):
                if self.path!='/fix':return self.reply(404)
                if not self.authorized():return self.reply(403)
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=2048:raise ValueError('Invalid body length')
                    self.connection.settimeout(3)
                    value=json.loads(self.rfile.read(length))
                    accepted=source.accept(value)
                except (ValueError,TypeError,KeyError,OSError):return self.reply(400)
                self.reply(204 if accepted else 202)
            def do_DELETE(self):
                if self.path!='/fix':return self.reply(404)
                if not self.authorized():return self.reply(403)
                with source.lock:source.fix=None
                self.reply(204)
            def log_message(self,*_):pass  # Do not log coordinates or credentials.
        self.server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
    def accept(self, value):
        if not isinstance(value,dict):raise ValueError('Invalid location')
        for key,minimum,maximum in [('latitude',-90,90),('longitude',-180,180),('accuracy',0,100000),('timestamp',0,float('inf'))]:
            n=value[key]
            if isinstance(n,bool) or not isinstance(n,(float,int)) or not math.isfinite(n) or not minimum<=n<=maximum:raise ValueError('Invalid location')
        for key,minimum,maximum in [('speed',0,1000),('heading',0,360),('altitude',-15000,100000)]:
            n=value.get(key)
            if n is not None and (isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or not minimum<=n<=maximum):raise ValueError('Invalid location')
        if not -5<=time.time()-value['timestamp']/1000<=30:return False
        with self.lock:self.fix=dict(value)
        return True
    def data(self):
        with self.lock:value=self.fix
        if value and -5<=time.time()-value['timestamp']/1000<=30:
            return frame(value['latitude'],value['longitude'],value.get('speed'),value.get('heading'),datetime.fromtimestamp(value['timestamp']/1000,timezone.utc),value.get('altitude'))
        return sentence('GPRMC,,V,,,,,,,,,,N')+sentence('GPGGA,,,,,,0,00,,,,,,,')+sentence('GPGSA,A,1,,,,,,,,,,,,,99.9,99.9,99.9')
    def __enter__(self):
        self.thread.start()
        print(f'Laptop browser URL: http://127.0.0.1:{self.server.server_port}/#token={self.token}',flush=True)
        return self
    def __exit__(self,*_):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)


def policy_write(path, content):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as pending:
        temporary = Path(pending.name)
        pending.write(content.encode())
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def serial_apparmor(device, profile=Path('/etc/apparmor.d/usr.sbin.gpsd')):
    """Grant only this owner PTY while the source runs; preserve local policy."""
    if not profile.exists():
        yield
        return
    if not re.fullmatch(r'/dev/pts/[0-9]+', device):
        raise ValueError('Expected a concrete virtual serial PTY')
    if profile.is_symlink() or not profile.is_file():
        raise RuntimeError('Unexpected gpsd AppArmor profile path')
    enabled=Path('/sys/module/apparmor/parameters/enabled')
    if enabled.exists() and enabled.read_text().strip()=='N':
        yield
        return
    parser=shutil.which('apparmor_parser')
    if os.geteuid()!=0 or not parser:
        raise RuntimeError('gpsd AppArmor support requires sudo and apparmor_parser')
    if not re.search(r'#?include\s+(?:if exists\s+)?[<"]local/usr\.sbin\.gpsd[>"]',profile.read_text()):
        raise RuntimeError('gpsd profile has no standard local include; refusing to change it')
    local=profile.parent/'local'/'usr.sbin.gpsd'
    if local.parent.is_symlink() or local.is_symlink():
        raise RuntimeError('Refusing symlinked AppArmor local policy')
    local.parent.mkdir(exist_ok=True)
    previous=local.read_text() if local.exists() else ''
    if '# BEGIN car-virtual-gps' in previous:
        raise RuntimeError('A previous virtual GPS policy remains; inspect local/usr.sbin.gpsd first')
    block=f'\n# BEGIN car-virtual-gps\nowner {device} rw,\n# END car-virtual-gps\n'
    def reload():
        subprocess.run([parser,'-r',str(profile)],check=True)
    try:
        policy_write(local,previous+block)
        reload()
        yield
    finally:
        # Remove our exact block only, retaining unrelated admin changes.
        current=local.read_text()
        if block not in current:
            raise RuntimeError('Virtual GPS AppArmor block changed externally; inspect local policy')
        policy_write(local,current.replace(block,'',1))
        reload()


@contextmanager
def source_lock(replace):
    if not replace:
        yield
        return
    # A separate lock prevents two controllers from stopping/restoring each other.
    fd = os.open('/run/lock/car-virtual-gps.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != 0:
            raise RuntimeError('System GPS source lock is not owned by root')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another system GPS source controller is already running')
        yield
    finally:
        os.close(fd)


@contextmanager
def system_source(replace):
    """Only the external source controller knows this is a test device."""
    active = []
    if replace:
        if os.geteuid() != 0:
            raise RuntimeError('--replace-system-gpsd requires sudo')
        for unit in ('gpsd.socket', 'gpsd.service'):
            result = subprocess.run(['systemctl', 'is-active', '--quiet', unit])
            if result.returncode == 0:
                active.append(unit)
    try:
        if replace:
            subprocess.run(['systemctl', 'stop', 'gpsd.socket', 'gpsd.service'], check=True)
        yield
    finally:
        # Do not enable disabled services or alter any persistent configuration.
        for unit in active:
            subprocess.run(['systemctl', 'start', unit], check=True)


def available(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        sock.bind(('127.0.0.1', port))


def run(args):
    gpsd = shutil.which('gpsd') or ('/usr/sbin/gpsd' if Path('/usr/sbin/gpsd').is_file() else None)
    if not args.serial_only and not gpsd:
        raise RuntimeError('Install gpsd first (Ubuntu: sudo apt install gpsd)')
    if not (-80 <= args.latitude <= 80 and -179 <= args.longitude <= 179):
        raise ValueError('Choose a route centre between ±80 latitude and ±179 longitude')
    if not (1 <= args.radius <= 5000 and 0 <= args.speed <= 80):
        raise ValueError('Radius must be 1–5000 m; speed must be 0–80 m/s')
    if args.replace_system_gpsd and (args.port != 2947 or args.serial_only):
        raise ValueError('System replacement uses gpsd port 2947')
    if args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds <= 0):
        raise ValueError('Duration must be positive')
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        with ExitStack() as stack:
            browser=stack.enter_context(BrowserSource(args.browser_port)) if args.browser else None
            stack.enter_context(source_lock(args.replace_system_gpsd))
            stack.enter_context(system_source(args.replace_system_gpsd))
            if not args.serial_only:
                available(args.port)  # Refuse to compete with an existing GPS service.
            master, slave = pty.openpty()
            process = None
            try:
                tty.setraw(slave)
                os.set_blocking(master, False)
                path = os.ttyname(slave)
                print(f'Virtual serial GPS: {path}', flush=True)
                if not args.serial_only:
                    credentials = {}
                    if os.geteuid() == 0:
                        uid = int(os.getenv('SUDO_UID', pwd.getpwnam('nobody').pw_uid))
                        account = pwd.getpwuid(uid)
                        if uid == 0:
                            raise RuntimeError('Launch with sudo from your normal user account')
                        os.chown(path, uid, account.pw_gid)
                        os.chmod(path, 0o600)
                        credentials = dict(user=uid, group=account.pw_gid, extra_groups=[])
                    stack.enter_context(serial_apparmor(path))
                    process = subprocess.Popen([gpsd, '-N', '-n', '-S', str(args.port), path], **credentials)
                    print(f'Normal gpsd endpoint: 127.0.0.1:{args.port}', flush=True)
                print('Virtual NMEA serial source active. Normal clients may record/transmit these positions. Ctrl+C restores the previous source.', flush=True)
                started = time.monotonic()
                next_frame = started
                while not stopped and (args.seconds is None or time.monotonic()-started < args.seconds):
                    if process and process.poll() is not None:
                        raise RuntimeError(f'gpsd exited with status {process.returncode}')
                    now = time.monotonic()
                    if now >= next_frame:
                        lat, lon, course = position(args.latitude, args.longitude, args.radius, args.speed, now-started)
                        try:
                            os.write(master, browser.data() if browser else frame(lat, lon, args.speed, course))
                        except BlockingIOError:
                            pass  # No reader: discard this output epoch, don't hang cleanup.
                        next_frame = now + 1
                    # Consume receiver probes just as a device would. No gpsd JSON is fabricated.
                    readable, _, _ = select.select([master], [], [], min(.1, max(0, next_frame-time.monotonic())))
                    if readable:
                        os.read(master, 4096)
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                os.close(master); os.close(slave)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latitude', type=float, default=39.0)
    parser.add_argument('--longitude', type=float, default=-77.0)
    parser.add_argument('--radius', type=float, default=300, help='Loop radius in metres')
    parser.add_argument('--speed', type=float, default=10, help='Speed in metres/second')
    parser.add_argument('--browser', action='store_true', help='Use laptop browser location as the external NMEA source')
    parser.add_argument('--browser-port',type=int,default=8790)
    parser.add_argument('--port', type=int, default=2947)
    parser.add_argument('--seconds', type=float, help='Stop and restore automatically after this many seconds')
    parser.add_argument('--serial-only', action='store_true', help='Expose NMEA PTY; let an external gpsd attach to the printed path')
    parser.add_argument('--replace-system-gpsd', action='store_true', help='Temporarily stop system gpsd, then restore previously active units on exit')
    args = parser.parse_args()
    if not all(1024 <= port <= 65535 for port in (args.port,args.browser_port)):
        parser.error('Use an unprivileged port between 1024 and 65535')
    try:
        run(args)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Virtual GPS: {exc}\n')


if __name__ == '__main__':
    main()
