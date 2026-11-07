"""Device-level input: real gpsd, unchanged recorder and normal HTTP adapter."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler
from recorder import virtual_gps as virtual

ROOT = Path(__file__).resolve().parents[1]


class VirtualGPS(unittest.TestCase):
    def test_nmea_checksum_coordinates_and_current_date(self):
        data = virtual.frame(-39.5, 78.25, 10, 90, datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))
        for line in data.decode().splitlines():
            body, checksum = line[1:].split('*')
            value = 0
            for c in body.encode():value ^= c
            self.assertEqual(checksum, f'{value:02X}')
        self.assertIn(b'3930.00000,S,07815.00000,E', data)
        self.assertIn(b'071126', data)
        self.assertEqual(virtual.coordinate(39.9999999999, True), ('4000.00000', 'N'))
        self.assertNotEqual(virtual.position(39, -77, 300, 10, 0), virtual.position(39, -77, 300, 10, 10))

    def test_controller_restores_only_previously_active_services_on_failure(self):
        calls=[]
        def systemctl(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0 if args[-1]=='gpsd.socket' else 3)
        with patch.object(virtual.os,'geteuid',return_value=0), patch.object(virtual.subprocess,'run',side_effect=systemctl):
            with self.assertRaisesRegex(RuntimeError,'test failure'):
                with virtual.system_source(True):raise RuntimeError('test failure')
        self.assertIn(['systemctl','start','gpsd.socket'],calls)
        self.assertNotIn(['systemctl','start','gpsd.service'],calls)

    @unittest.skipUnless(shutil.which('gpsd'), 'Optional real gpsd integration requires gpsd')
    def test_real_parser_recorder_and_ha_http_adapter(self):
        try:virtual.available(2947)
        except OSError:self.skipTest('A real GPS service is already using port 2947')
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'gps.sqlite3'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env={**os.environ,'CAR_DB':directory+'/vehicle.sqlite3','CAR_GPS_DB':str(db),'CAR_PORT':str(port),'CAR_OVERRIDE_DIR':directory+'/overrides'}
            commands=[
                [sys.executable,str(ROOT/'recorder/virtual_gps.py'),'--seconds','40'],
                [sys.executable,str(ROOT/'recorder/gps_recorder.py')],
                [sys.executable,'-m','app.server'],
            ]
            client=build_opener(ProxyHandler({}))
            processes=[]
            try:
                for command in commands:
                    processes.append(subprocess.Popen(command,cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
                deadline=time.monotonic()+30
                previous=None
                while time.monotonic()<deadline:
                    try:
                        with client.open(f'http://127.0.0.1:{port}/api/home-assistant/state',timeout=3) as response:
                            snapshot=json.load(response)
                        if snapshot['gps']:
                            with sqlite3.connect(db) as connection:
                                fixes=connection.execute('SELECT lat,lon FROM fixes ORDER BY id').fetchall()
                            if len(fixes)>=2 and fixes[0]!=fixes[-1]:
                                self.assertEqual(snapshot['gps']['mode'],3)
                                self.assertGreater(len(fixes),1)
                                self.assertTrue(all(process.poll() is None for process in processes))
                                return
                    except (OSError, ValueError, sqlite3.Error):pass
                    time.sleep(.25)
                self.fail('No moving GPS fixes through the real gpsd → recorder → HA adapter pipeline')
            finally:
                for process in reversed(processes):
                    if process.poll() is None:process.terminate()
                for process in reversed(processes):
                    try:process.wait(timeout=8)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
