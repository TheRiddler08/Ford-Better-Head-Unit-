"""Fixed-gateway reception evidence from authenticated ChirpStack events.

No timeout is interpreted as a negative RF measurement. Unknown/mobile gateway
roles are retained for audit but never used to estimate fixed-site coverage.
"""
import json
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import time


def database(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=15)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    db.execute('''CREATE TABLE IF NOT EXISTS coverage(id INTEGER PRIMARY KEY,
        event_id TEXT, gateway TEXT, received REAL, lat REAL, lon REAL,
        rssi REAL, snr REAL, eligible INTEGER, reason TEXT, raw TEXT,
        UNIQUE(event_id,gateway))''')
    return db


def numeric(value,low,high):
    if isinstance(value,bool):raise ValueError('Boolean coordinate')
    value=float(value)
    if not math.isfinite(value) or not low<=value<=high:raise ValueError('Invalid coordinate')
    return value


def ingest(path,wire,registry,received=None):
    """Consume decoder object carGpsV1; unknown event/role never becomes coverage.

    GPS and gateway-mode timestamps must come from the same transmitted sample.
    ChirpStack deduplicationId provides restart-safe evidence deduplication.
    """
    sample=wire.get('object',{}).get('carGpsV1')
    if not isinstance(sample,dict):return 0
    lat=numeric(sample.get('lat'),-90,90);lon=numeric(sample.get('lon'),-180,180)
    received=time.time() if received is None else received
    stamp=numeric(sample.get('ts'),0,1e12)
    mode_stamp=numeric(sample.get('gatewayModeAt'),0,1e12)
    event=wire.get('deduplicationId')
    if not isinstance(event,str) or not 1<=len(event)<=128:raise ValueError('Missing unique network event ID')
    # A claimed false flag without a known profile and fresh role observation is
    # insufficient; the registry is administrator-controlled, not packet data.
    mode=sample.get('gatewayMode')
    reason='eligible'
    if mode!='client':reason='mobile_gateway_active' if mode=='gateway' else 'gateway_mode_unknown'
    elif abs(stamp-mode_stamp)>30:reason='gateway_mode_stale'
    elif not 0<=received-stamp<=300:reason='gps_stale_or_clock_invalid'
    count=0
    with closing(database(path)) as db, db:
        for rx in wire.get('rxInfo',[])[:64]:
            gateway=str(rx.get('gatewayId','')).lower()
            role=registry.get(gateway,{}).get('role')
            why=reason if role=='fixed' else 'mobile_gateway_receiver' if role=='mobile' else 'gateway_unregistered'
            cursor=db.execute('''INSERT OR IGNORE INTO coverage
                (event_id,gateway,received,lat,lon,rssi,snr,eligible,reason,raw)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',(event,gateway,received,lat,lon,
                numeric(rx['rssi'],-300,100) if 'rssi' in rx else None,
                numeric(rx['snr'],-100,100) if 'snr' in rx else None,
                int(why=='eligible'),why,json.dumps({'sample':sample,'rxInfo':rx},allow_nan=False)))
            count+=cursor.rowcount
    return count


def report(path,after=0):
    with closing(database(path)) as db, db:
        items=[dict(row) for row in db.execute('SELECT * FROM coverage WHERE id>? ORDER BY id LIMIT 2001',(after,))]
        more=len(items)>2000;items=items[:2000]
        totals={row['reason']:row['n'] for row in db.execute('SELECT reason,COUNT(*) n FROM coverage GROUP BY reason')}
    return {'items':items,'next':items[-1]['id'] if items else after,'hasMore':more,'totals':totals,
            'meaning':'Observed receptions only. Untested locations and missing uplinks are unknown.'}
