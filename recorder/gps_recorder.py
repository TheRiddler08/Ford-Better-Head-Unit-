#!/usr/bin/env python3
"""Persistent local gpsd journal. Records real fixes; never interpolates gaps."""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import signal
import socket
import sqlite3
import time

STOP = False

def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000*2*math.asin(min(1, math.sqrt(h)))


def open_db(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS gps_messages(id INTEGER PRIMARY KEY, received REAL, class TEXT, raw TEXT);
      CREATE TABLE IF NOT EXISTS fixes(id INTEGER PRIMARY KEY, received REAL, ts REAL,
        time_source TEXT, lat REAL, lon REAL, altitude REAL, speed REAL, heading REAL,
        mode INTEGER, epx REAL, epy REAL, segment INTEGER, break_reason TEXT);
      CREATE INDEX IF NOT EXISTS fixes_time ON fixes(ts);
      CREATE TABLE IF NOT EXISTS stops(id INTEGER PRIMARY KEY, started REAL, ended REAL,
        lat REAL, lon REAL, samples INTEGER, segment INTEGER);
      CREATE TABLE IF NOT EXISTS gps_state(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT);
      CREATE TABLE IF NOT EXISTS gps_events(id INTEGER PRIMARY KEY, ts REAL, name TEXT, detail TEXT);
    ''')
    return db


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def parse_fix(message, received):
    if not isinstance(message, dict) or message.get('class') != 'TPV':
        return None
    mode = message.get('mode')
    lat, lon = finite(message.get('lat')), finite(message.get('lon'))
    if type(mode) is not int or mode not in (2, 3) or lat is None or lon is None or not (-90<=lat<=90 and -180<=lon<=180):
        return None
    ts = None
    try:
        stamp = datetime.fromisoformat(str(message.get('time')).replace('Z','+00:00'))
        if stamp.tzinfo is not None:
            ts = stamp.timestamp()
    except (ValueError, OverflowError, OSError):
        pass
    return {'received':received, 'ts':ts if ts is not None else received,
            'time_source':'gps' if ts is not None else 'receipt', 'lat':lat, 'lon':lon,
            'altitude':finite(message.get('altMSL',message.get('alt'))),
            'speed':finite(message.get('speed')), 'heading':finite(message.get('track')),
            'mode':mode, 'epx':finite(message.get('epx')), 'epy':finite(message.get('epy'))}


def record_message(db, message, received=None):
    received = time.time() if received is None else received
    raw = json.dumps(message, allow_nan=False)
    db.execute('INSERT INTO gps_messages(received,class,raw) VALUES(?,?,?)',
               (received,message.get('class','unknown') if isinstance(message,dict) else 'unknown',raw))
    fix = parse_fix(message, received)
    if fix is None:
        db.commit()
        return None
    saved = db.execute('SELECT value FROM gps_state WHERE id=1').fetchone()
    state = json.loads(saved[0]) if saved else {}
    previous = state.get('last')
    reason = 'start' if previous is None else ''
    if previous:
        dt = fix['ts']-previous['ts']
        metres = distance((previous['lat'],previous['lon']),(fix['lat'],fix['lon']))
        if dt < 0:reason='clock_reset'
        elif dt > 120 or received-previous['received'] > 120:reason='gap'
        elif abs(fix['lon']-previous['lon'])>180:reason='date_line'
        elif metres > max(100, max(dt,0)*120):reason='position_jump'
    segment = state.get('segment',0)+(1 if reason else 0)
    fix.update(segment=segment,break_reason=reason)
    columns = list(fix)
    db.execute('INSERT INTO fixes('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',list(fix.values()))
    candidate = state.get('candidate')
    if reason or candidate is None or distance((candidate['lat'],candidate['lon']),(fix['lat'],fix['lon'])) > 75:
        candidate = {'lat':fix['lat'],'lon':fix['lon'],'started':fix['ts'],'samples':0,'stop_id':None}
    candidate['samples'] += 1
    if fix['ts']-candidate['started'] >= 180:
        values = (candidate['started'],fix['ts'],candidate['lat'],candidate['lon'],candidate['samples'],segment)
        if candidate['stop_id'] is None:
            candidate['stop_id'] = db.execute('INSERT INTO stops(started,ended,lat,lon,samples,segment) VALUES(?,?,?,?,?,?)',values).lastrowid
        else:
            db.execute('UPDATE stops SET ended=?,samples=? WHERE id=?',(fix['ts'],candidate['samples'],candidate['stop_id']))
    state = {'last':fix,'segment':segment,'candidate':candidate}
    db.execute('INSERT OR REPLACE INTO gps_state VALUES(1,?)',(json.dumps(state),))
    db.commit()
    return fix


def event(db, name, detail):
    db.execute('INSERT INTO gps_events(ts,name,detail) VALUES(?,?,?)',(time.time(),name,detail))
    db.commit()
    print(name+': '+detail,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    default=Path(os.getenv('CAR_DB',str(Path.home()/'car-console/obd-data/vehicle.sqlite3'))).with_name('gps.sqlite3')
    parser.add_argument('--db',default=os.getenv('CAR_GPS_DB',str(default)))
    args=parser.parse_args()
    global STOP
    def stop(*_):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    db=open_db(args.db)
    # A second process must not duplicate the history.
    import fcntl
    lock=Path(str(Path(args.db).expanduser())+'.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        while not STOP:
            try:
                with socket.create_connection(('127.0.0.1',2947),timeout=3) as sock:
                    sock.settimeout(2)
                    sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
                    event(db,'connected','Local gpsd watch established')
                    buffer=b''
                    while not STOP:
                        try:chunk=sock.recv(8192)
                        except socket.timeout:continue
                        if not chunk:raise OSError('gpsd closed connection')
                        buffer+=chunk
                        if len(buffer)>262144:raise ValueError('gpsd message exceeds buffer limit')
                        while b'\n' in buffer:
                            line,buffer=buffer.split(b'\n',1)
                            if len(line)>65536:
                                event(db,'invalid_message','gpsd line exceeds 64 KB');continue
                            try:record_message(db,json.loads(line))
                            except (ValueError,TypeError,UnicodeError):event(db,'invalid_message','Malformed gpsd message')
            except (OSError,ValueError) as exc:
                event(db,'disconnected',str(exc))
                for _ in range(10):
                    if STOP:break
                    time.sleep(.5)
    finally:
        db.close();lock.close()


if __name__=='__main__':main()
