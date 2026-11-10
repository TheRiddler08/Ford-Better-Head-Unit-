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
import hashlib
import re


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


def gateway_view(registry):
    sites={}
    for ident,site in registry.items():
        if not isinstance(ident,str) or not re.fullmatch('[a-fA-F0-9]{16}',ident) or not isinstance(site,dict):continue
        position=site if site.get('lat') is not None and site.get('lon') is not None else site.get('location') if isinstance(site.get('location'),dict) else site
        value={'name':str(site.get('name') or ident)[:160],'role':site.get('role')}
        try:value.update(lat=numeric(position.get('lat',position.get('latitude')),-90,90),lon=numeric(position.get('lon',position.get('longitude')),-180,180))
        except (ValueError,TypeError):pass
        sites[ident.lower()]=value
    return sites


def register_gateway(path,data):
    """Home-owner registration; never callable with the read-only device token."""
    if not isinstance(data,dict) or set(data)!={'id','name','role','lat','lon'}:raise ValueError('Invalid gateway registration')
    if not isinstance(data['id'],str):raise ValueError('Invalid gateway ID')
    ident=data['id'].lower()
    if not re.fullmatch('[a-f0-9]{16}',ident) or data['role'] not in ('fixed','mobile','unknown'):raise ValueError('Invalid gateway ID or role')
    if not isinstance(data['name'],str) or len(data['name'])>160:raise ValueError('Invalid gateway name')
    position={}
    if data['lat'] is not None or data['lon'] is not None:position={'lat':numeric(data['lat'],-90,90),'lon':numeric(data['lon'],-180,180)}
    path=Path(path);registry=json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(registry,dict) or ident not in registry and len(registry)>=100:raise ValueError('Gateway registry exceeds limit')
    site=dict(registry.get(ident,{}));site.update(name=data['name'],role=data['role'],**position)
    if not position:
        for key in ('lat','lon','latitude','longitude'):site.pop(key,None)
        if isinstance(site.get('location'),dict):site['location']={key:value for key,value in site['location'].items() if key not in ('lat','lon','latitude','longitude')}
    registry[ident]=site
    from control.update_agent import atomic
    atomic(path,json.dumps(registry,allow_nan=False).encode())
    return {'gateways':gateway_view(registry)}


def map_report(path,registry,after=0):
    """Device-scoped map feed. No raw packets, tokens or unrelated device state."""
    with closing(database(path)) as db:
        first=db.execute('SELECT event_id,gateway,received FROM coverage ORDER BY id LIMIT 1').fetchone()
        instance=hashlib.sha256(json.dumps(list(first) if first else []).encode()).hexdigest()
        maximum=db.execute('SELECT COALESCE(MAX(id),0) FROM coverage').fetchone()[0]
        if after>maximum:after=0
        rows=[dict(row) for row in db.execute('SELECT id,gateway,received,lat,lon,rssi,snr,eligible,reason FROM coverage WHERE id>? ORDER BY id LIMIT 2001',(after,))]
    sites=gateway_view(registry)
    more=len(rows)>2000;rows=rows[:2000]
    return {'instance':instance,'gateways':sites,'items':rows,'next':rows[-1]['id'] if rows else after,'hasMore':more}
