#!/usr/bin/env python3
"""Persistent local gpsd journal. Records real fixes; never interpolates gaps."""
import argparse
import base64
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
    fresh=not path.exists()
    db = sqlite3.connect(path, timeout=15)
    if fresh:path.chmod(0o600)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS gps_stream(id INTEGER PRIMARY KEY, received REAL, session INTEGER, raw_b64 TEXT);
      CREATE TABLE IF NOT EXISTS gps_sessions(id INTEGER PRIMARY KEY, started REAL, ended REAL, reason TEXT);
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


def record_message(db, message, received=None, commit=True):
    received = time.time() if received is None else received
    raw = json.dumps(message, allow_nan=False)
    db.execute('INSERT INTO gps_messages(received,class,raw) VALUES(?,?,?)',
               (received,message.get('class','unknown') if isinstance(message,dict) else 'unknown',raw))
    fix = parse_fix(message, received)
    saved = db.execute('SELECT value FROM gps_state WHERE id=1').fetchone()
    state = json.loads(saved[0]) if saved else {}
    if fix is None:
        if isinstance(message,dict) and message.get('class')=='TPV':
            state['force_break']='fix_lost'
            db.execute('INSERT OR REPLACE INTO gps_state VALUES(1,?)',(json.dumps(state),))
        if commit:db.commit()
        return None
    previous = state.get('last')
    reason = 'start' if previous is None else ''
    if previous:
        dt = fix['ts']-previous['ts']
        metres = distance((previous['lat'],previous['lon']),(fix['lat'],fix['lon']))
        if dt < 0:reason='clock_reset'
        elif dt > 120 or received-previous['received'] > 120:reason='gap'
        elif abs(fix['lon']-previous['lon'])>180:reason='date_line'
        elif metres > max(100, max(dt,0)*120):reason='position_jump'
    reason=state.get('force_break') or reason
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
    if commit:db.commit()
    return fix


def start_session(db, received=None):
    received=time.time() if received is None else received
    with db:
        db.execute("UPDATE gps_sessions SET ended=?,reason='interrupted' WHERE ended IS NULL",(received,))
        session=db.execute('INSERT INTO gps_sessions(started) VALUES(?)',(received,)).lastrowid
        saved=db.execute('SELECT value FROM gps_state WHERE id=1').fetchone()
        state=json.loads(saved[0]) if saved else {}
        state['force_break']='connection_start'
        db.execute('INSERT OR REPLACE INTO gps_state VALUES(1,?)',(json.dumps(state),))
    return session


def end_session(db, session, reason, received=None):
    if session is not None:
        with db:db.execute('UPDATE gps_sessions SET ended=?,reason=? WHERE id=? AND ended IS NULL',
                           (time.time() if received is None else received,reason,session))


def ingest_chunk(db, session, buffer, chunk, received=None):
    """Durably retain exact gpsd bytes, including malformed/partial JSON."""
    received=time.time() if received is None else received
    with db:
        db.execute('INSERT INTO gps_stream(received,session,raw_b64) VALUES(?,?,?)',
                   (received,session,base64.b64encode(chunk).decode('ascii')))
        buffer+=chunk
        while b'\n' in buffer:
            line,buffer=buffer.split(b'\n',1)
            try:
                if len(line)>65536:raise ValueError('Oversized gpsd line')
                record_message(db,json.loads(line),received,commit=False)
            except (ValueError,TypeError,UnicodeError):
                db.execute('INSERT INTO gps_events(ts,name,detail) VALUES(?,?,?)',
                           (received,'invalid_message','Malformed or oversized gpsd line; bytes retained'))
        if len(buffer)>262144:
            db.execute('INSERT INTO gps_events(ts,name,detail) VALUES(?,?,?)',
                       (received,'invalid_message','Unterminated gpsd line; bytes retained, parser reset'))
            buffer=b''
    return buffer


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
        session=None
        while not STOP:
            try:
                with socket.create_connection(('127.0.0.1',2947),timeout=3) as sock:
                    sock.settimeout(2)
                    sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
                    event(db,'connected','Local gpsd watch established')
                    session=start_session(db)
                    buffer=b''
                    while not STOP:
                        try:chunk=sock.recv(8192)
                        except socket.timeout:continue
                        if not chunk:raise OSError('gpsd closed connection')
                        buffer=ingest_chunk(db,session,buffer,chunk)
            except (OSError,ValueError) as exc:
                end_session(db,session,'disconnected');session=None
                event(db,'disconnected',str(exc))
                for _ in range(10):
                    if STOP:break
                    time.sleep(.5)
    finally:
        end_session(db,session,'stopped')
        db.close();lock.close()


if __name__=='__main__':
    overlay=Path(os.getenv('CAR_OVERRIDE_DIR','/var/lib/car-dashboard/overrides'))/'recorder/gps_recorder.py'
    if overlay.is_file() and not overlay.is_symlink() and os.getenv('CAR_RECORDER_OVERLAY_ACTIVE')!='1':
        import runpy
        os.environ['CAR_RECORDER_OVERLAY_ACTIVE']='1'
        runpy.run_path(str(overlay),run_name='__main__')
    else:
        main()
