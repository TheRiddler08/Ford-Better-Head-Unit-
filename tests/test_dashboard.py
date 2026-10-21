"""Synthetic-schema tests; do not touch the car's actual SQLite database."""
import importlib.util
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / 'app' / 'server.py'
spec = importlib.util.spec_from_file_location('car_dashboard', SRC)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'sample.sqlite3'
        db = sqlite3.connect(self.path)
        db.executescript('''
          CREATE TABLE readings(ts INTEGER, name TEXT, value TEXT, number REAL, unit TEXT);
          CREATE TABLE snapshots(id INTEGER PRIMARY KEY, stage TEXT, started INTEGER, total INTEGER, successes INTEGER);
          CREATE TABLE events(ts INTEGER, name TEXT, detail TEXT);
        ''')
        now = int(time.time())
        self.t0 = now-1500
        for i in range(9):
            ts = self.t0+i*15
            db.execute('INSERT INTO readings VALUES (?,?,?,?,?)',(ts,'SPEED',f'{10+i} km/h',10+i,'kilometer_per_hour'))
            db.execute('INSERT INTO readings VALUES (?,?,?,?,?)',(ts,'RPM',f'{800+i*90} revolutions_per_minute',800+i*90,'revolutions_per_minute'))
        db.execute('INSERT INTO readings VALUES (?,?,?,?,?)',(self.t0+70,'FUEL_LEVEL','12 percent',12,'percent'))
        db.execute('INSERT INTO readings VALUES (?,?,?,?,?)',(self.t0+80,'FUEL_LEVEL','0 percent',0,'percent'))
        db.execute('INSERT INTO snapshots VALUES (1,?,?,?,?)',('connect',self.t0,69,69))
        db.execute('INSERT INTO events VALUES (?,?,?)',(self.t0,'connected','CAN'))
        db.commit()
        db.close()
        self.old = app.DB_PATH
        app.DB_PATH = self.path
        self.addCleanup(setattr,app,'DB_PATH',self.old)

    def test_works_readonly(self):
        db = app.connect()
        with self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM readings')
        db.close()
        self.assertEqual(sqlite3.connect(self.path).execute('SELECT COUNT(*) FROM readings').fetchone()[0],20)

    def test_live_and_summary(self):
        with app.connect() as db:
            latest = app.route_live(db)
            self.assertEqual(latest['sensors']['RPM']['number'],1520)
            overview = app.route_summary(db)
            self.assertEqual(overview['totalReadings'],20)
            self.assertEqual(overview['sensorCount'],3)
            self.assertEqual(overview['snapshotCount'],1)

    def test_history_bucketing(self):
        with app.connect() as db:
            result = app.route_history(db, {'name':['SPEED'], 'period':['1h']})
            self.assertGreater(len(result['points']),0)
            self.assertLessEqual(len(result['points']),300)
            self.assertGreaterEqual(max(x['number'] for x in result['points']),10)

    def test_session_and_distance(self):
        with app.connect() as db:
            sessions=app.route_trips(db)
            self.assertEqual(len(sessions),1)
            self.assertEqual(sessions[0]['speedSamples'],9)
            self.assertGreater(sessions[0]['distanceKmEstimate'],0)

    def test_fuel_zero_heuristic(self):
        with app.connect() as db:
            alert=app.route_alerts(db)
            self.assertEqual(alert[0]['code'],'fuel_zero')

    def test_missing_database(self):
        app.DB_PATH=Path(self.tmp.name)/'missing.sqlite3'
        with self.assertRaises(FileNotFoundError):
            app.connect()


if __name__ == '__main__':
    unittest.main()
