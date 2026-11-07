#!/usr/bin/env python3
"""Independent NMEA serial GPS source. No dashboard/recorder simulation API."""
import argparse
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import pty
import pwd
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


def frame(lat, lon, speed, course, stamp=None):
    stamp = stamp or datetime.now(timezone.utc)
    a, ns = coordinate(lat, True)
    b, ew = coordinate(lon, False)
    clock = stamp.strftime('%H%M%S') + f'.{stamp.microsecond//10000:02d}'
    return b''.join([
        sentence(f'GPRMC,{clock},A,{a},{ns},{b},{ew},{speed/0.514444:.2f},{course:.2f},{stamp:%d%m%y},,,A'),
        sentence(f'GPGGA,{clock},{a},{ns},{b},{ew},1,08,0.9,100.0,M,0.0,M,,'),
        sentence('GPGSA,A,3,01,02,03,04,05,06,07,08,,,,,1.5,0.9,1.2'),
    ])


def position(lat, lon, radius, speed, elapsed):
    angle = elapsed * speed / radius
    north, east = radius * math.sin(angle), radius * math.cos(angle)
    return lat + north/111320, lon + east/(111320*math.cos(math.radians(lat))), math.degrees(-angle) % 360


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
        with source_lock(args.replace_system_gpsd), system_source(args.replace_system_gpsd):
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
                    process = subprocess.Popen([gpsd, '-N', '-n', '-S', str(args.port), path], **credentials)
                    print(f'Normal gpsd endpoint: 127.0.0.1:{args.port}', flush=True)
                print('Synthetic NMEA source active. Normal clients may record/transmit these positions. Ctrl+C restores the previous source.', flush=True)
                started = time.monotonic()
                next_frame = started
                while not stopped and (args.seconds is None or time.monotonic()-started < args.seconds):
                    if process and process.poll() is not None:
                        raise RuntimeError(f'gpsd exited with status {process.returncode}')
                    now = time.monotonic()
                    if now >= next_frame:
                        lat, lon, course = position(args.latitude, args.longitude, args.radius, args.speed, now-started)
                        try:
                            os.write(master, frame(lat, lon, args.speed, course))
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
    parser.add_argument('--port', type=int, default=2947)
    parser.add_argument('--seconds', type=float, help='Stop and restore automatically after this many seconds')
    parser.add_argument('--serial-only', action='store_true', help='Expose NMEA PTY; let an external gpsd attach to the printed path')
    parser.add_argument('--replace-system-gpsd', action='store_true', help='Temporarily stop system gpsd, then restore previously active units on exit')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged port between 1024 and 65535')
    try:
        run(args)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Virtual GPS: {exc}\n')


if __name__ == '__main__':
    main()
