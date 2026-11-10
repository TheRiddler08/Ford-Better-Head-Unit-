"""Local navigation overlays. Public USA camera data and authenticated RF evidence.

The public download URL is constant: GPS, viewport and routes never go to it.
Bulk data uses IP only; this worker never queues radio frames or enables GPS TX.
"""
from contextlib import closing
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.request import Request,build_opener
from control.tailscale_agent import NoRedirect

CAMERA_URL='https://raw.githubusercontent.com/FoggedLens/deflockhopper_maps/master/public/cameras-us.json.gz'
ATTRIBUTION='Camera locations © OpenStreetMap contributors, ODbL; USA export by DeFlock / FlockHopper'
LIMIT=32*1024**2
MAX_CAMERAS=200000


def location(home=None):return (Path(home) if home else Path.home())/'.local/share/drive-console/map-layers.sqlite3'


def db(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():raise ValueError('Refusing symlinked map cache')
    connection=sqlite3.connect(path,timeout=15);connection.row_factory=sqlite3.Row
    path.chmod(0o600)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS cameras(id TEXT PRIMARY KEY,lat REAL,lon REAL,label TEXT,brand TEXT,operator TEXT,direction REAL,osm_type TEXT,osm_id TEXT);
    CREATE INDEX IF NOT EXISTS camera_bounds ON cameras(lat,lon);
    CREATE TABLE IF NOT EXISTS gateways(id TEXT PRIMARY KEY,label TEXT,lat REAL,lon REAL,role TEXT,radius REAL DEFAULT 0,manual INTEGER DEFAULT 0,hidden INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS receptions(id INTEGER PRIMARY KEY,gateway TEXT,received REAL,lat REAL,lon REAL,rssi REAL,snr REAL);
    CREATE INDEX IF NOT EXISTS reception_bounds ON receptions(lat,lon);
    ''')
    return connection


def get_meta(connection,key,default=None):
    row=connection.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default


def put_meta(connection,key,value):connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value,allow_nan=False)))


def number(value,low,high):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:raise ValueError('Invalid map number')
    return value


def point(value):return number(value.get('lat'),-90,90),number(value.get('lon'),-180,180)


def label(value,limit=160):return str(value or '').replace('\x00','')[:limit]


def cameras(document):
    if isinstance(document,dict):rows=document.get('features',document.get('elements',document.get('cameras')))
    else:rows=document
    if not isinstance(rows,list) or len(rows)>MAX_CAMERAS:raise ValueError('Expected a bounded GeoJSON, OSM or DeFlock camera export')
    result=[];seen=set()
    for item in rows:
        if not isinstance(item,dict):raise ValueError('Invalid camera entry')
        if item.get('type')=='Feature':
            geo=item.get('geometry') or {}
            if geo.get('type')!='Point':continue
            coords=geo.get('coordinates',[])
            if len(coords)<2:raise ValueError('Invalid camera geometry')
            props=item.get('properties') or {};lat,lon=point({'lat':coords[1],'lon':coords[0]})
        else:props=dict(item.get('tags') or {},**item);lat,lon=point(item)
        brand=label(props.get('brand') or props.get('manufacturer') or props.get('surveillance:manufacturer') or props.get('surveillance:brand'),100)
        operator=label(props.get('operator') or props.get('surveillance:operator'))
        raw_id=str(props.get('osmId',props.get('osm_id',props.get('@id',props.get('id','')))))
        kind=props.get('osmType',props.get('osm_type',props.get('type','node')))
        if '/' in raw_id:kind,raw_id=raw_id.split('/',1)
        osm_id=raw_id if re.fullmatch('[0-9]{1,20}',raw_id) else ''
        kind=kind if kind in ('node','way','relation') else 'node'
        ident=kind+':'+osm_id if osm_id else hashlib.sha256(json.dumps([lat,lon,brand,operator]).encode()).hexdigest()[:24]
        if ident in seen:continue
        seen.add(ident)
        direction=props.get('direction',props.get('camera:direction'))
        try:direction=float(direction);direction=direction%360 if math.isfinite(direction) else None
        except (ValueError,TypeError):direction=None
        result.append((ident,lat,lon,label(props.get('name')) or brand or 'Reported ALPR camera',brand,operator,direction,kind,osm_id))
    return result


def import_cameras(path,document,source='Local import',now=None):
    rows=cameras(document)
    # Empty/error downloads must never erase a useful offline cache.
    if not rows:raise ValueError('Camera export contains no locations; existing cache retained')
    with closing(db(path)) as connection,connection:
        connection.execute('DELETE FROM cameras')
        connection.executemany('INSERT INTO cameras VALUES(?,?,?,?,?,?,?,?,?)',rows)
        put_meta(connection,'cameras',{'phase':'cached','count':len(rows),'fetchedAt':time.time() if now is None else now,'source':source,'attribution':ATTRIBUTION})
    return len(rows)


def seed_snapshot(path,repo):
    with closing(db(path)) as connection:
        if get_meta(connection,'cameras'):return
    directory=Path(repo)/'data/map-layers';metadata=json.loads((directory/'snapshot.json').read_text())
    raw=(directory/'cameras-us.json.gz').read_bytes()
    if len(raw)>8*1024**2 or hashlib.sha256(raw).hexdigest()!=metadata.get('sha256'):raise ValueError('Bundled camera snapshot checksum mismatch')
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:decoded=stream.read(LIMIT+1)
    if len(decoded)>LIMIT:raise ValueError('Bundled camera snapshot exceeds limit')
    import_cameras(path,json.loads(decoded),'Bundled DeFlock / FlockHopper USA snapshot',now=0)
    with closing(db(path)) as connection,connection:
        value=get_meta(connection,'cameras');value.update(sourceDate=metadata.get('sourceDate'),snapshotRevision=metadata.get('revision'))
        put_meta(connection,'cameras',value);put_meta(connection,'bundled-snapshot',metadata)


def import_coverage(path,data):
    if not isinstance(data,dict) or not isinstance(data.get('instance'),str) or not re.fullmatch('[a-f0-9]{64}',data['instance']):raise ValueError('Invalid coverage snapshot')
    sites=data.get('gateways');rows=data.get('items')
    if not isinstance(sites,dict) or len(sites)>100 or not isinstance(rows,list) or len(rows)>2000:raise ValueError('Coverage response exceeds bounds')
    normalized=[]
    for row in rows:
        if not isinstance(row,dict) or row.get('eligible') not in (1,True) or row.get('reason')!='eligible':continue
        gateway=row.get('gateway')
        if not isinstance(sites.get(gateway),dict) or sites[gateway].get('role')!='fixed':continue
        lat,lon=point(row);ident=row['id']
        if type(ident) is not int or ident<1:raise ValueError('Invalid reception ID')
        normalized.append((ident,gateway,number(row['received'],0,1e12),lat,lon,
            number(row['rssi'],-300,100) if row.get('rssi') is not None else None,
            number(row['snr'],-100,100) if row.get('snr') is not None else None))
    cursor=data.get('next')
    if type(cursor) is not int or cursor<0:raise ValueError('Invalid coverage cursor')
    with closing(db(path)) as connection,connection:
        if get_meta(connection,'coverage-instance')!=data['instance']:connection.execute('DELETE FROM receptions')
        # Hide old evidence if a registered gateway stops being a fixed site.
        connection.execute("UPDATE gateways SET role='unknown' WHERE manual=0 OR id IN (SELECT gateway FROM receptions)")
        for ident,site in sites.items():
            if not re.fullmatch('[0-9a-fA-F]{16}',ident) or not isinstance(site,dict):continue
            try:lat,lon=point(site)
            except ValueError:lat=lon=None
            role=site.get('role') if site.get('role') in ('fixed','mobile') else 'unknown'
            connection.execute('''INSERT INTO gateways(id,label,lat,lon,role) VALUES(?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET role=excluded.role,
              label=CASE WHEN gateways.manual=1 THEN gateways.label ELSE excluded.label END,
              lat=CASE WHEN gateways.manual=1 THEN gateways.lat ELSE excluded.lat END,
              lon=CASE WHEN gateways.manual=1 THEN gateways.lon ELSE excluded.lon END''',
              (ident.lower(),label(site.get('name') or ident),lat,lon,role))
        connection.executemany('INSERT OR REPLACE INTO receptions VALUES(?,?,?,?,?,?,?)',normalized)
        connection.execute('DELETE FROM receptions WHERE id NOT IN (SELECT id FROM receptions ORDER BY id DESC LIMIT 200000)')
        put_meta(connection,'coverage-instance',data['instance']);put_meta(connection,'coverage-cursor',cursor)
        put_meta(connection,'fixed-gateways',[ident.lower() for ident,site in sites.items() if isinstance(site,dict) and site.get('role')=='fixed' and re.fullmatch('[a-fA-F0-9]{16}',ident)])
        put_meta(connection,'coverage',{'phase':'cached','syncedAt':time.time(),'source':'Authenticated home ChirpStack reception history',
            'meaning':'Positive reception evidence only. Untested locations and missing uplinks are unknown.'})


def action(path,document):
    if not isinstance(document,dict):raise ValueError('Expected layer action')
    kind=document.get('action')
    if kind=='import-cameras':return {'imported':import_cameras(path,document.get('data'))}
    with closing(db(path)) as connection,connection:
        if kind=='refresh':
            last=get_meta(connection,'refresh-request',0)
            if time.time()-last<60:return {'phase':'already-requested'}
            put_meta(connection,'refresh-request',time.time());return {'phase':'queued'}
        if kind not in ('gateway','hide-gateway'):raise ValueError('Unknown map layer action')
        ident=document.get('id','').lower()
        if not re.fullmatch('[a-f0-9]{16}',ident):raise ValueError('Use a 16-digit gateway EUI')
        if kind=='hide-gateway':connection.execute('UPDATE gateways SET hidden=1 WHERE id=?',(ident,));return {'phase':'hidden'}
        lat,lon=point(document);role=document.get('role')
        if role not in ('fixed','mobile'):raise ValueError('Choose fixed or mobile gateway')
        radius=number(document.get('radius',0),0,200)
        connection.execute('''INSERT INTO gateways VALUES(?,?,?,?,?,?,1,0) ON CONFLICT(id) DO UPDATE SET
            label=excluded.label,lat=excluded.lat,lon=excluded.lon,role=excluded.role,radius=excluded.radius,manual=1,hidden=0''',
            (ident,label(document.get('name') or ident),lat,lon,role,radius))
        return {'phase':'saved'}


def distance(a,b,c,d):
    lat1,lat2=math.radians(a),math.radians(c);delta=math.radians(d-b)
    h=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(delta/2)**2
    return 6371000*2*math.asin(min(1,math.sqrt(max(0,h))))


def query(path,options):
    def val(key,default):return options.get(key,[str(default)])[0]
    west,east=number(float(val('west',-180)),-180,180),number(float(val('east',180)),-180,180)
    south,north=number(float(val('south',-90)),-90,90),number(float(val('north',90)),-90,90)
    if south>=north:raise ValueError('Invalid map bounds')
    zoom=int(val('zoom',5));number(zoom,2,18)
    enabled=set(val('layers','').split(','))-{''}
    if enabled-{'cameras','coverage','estimates'}:raise ValueError('Unknown map layer')
    manufacturer=val('manufacturer','flock')
    if manufacturer not in ('all','flock'):raise ValueError('Unknown camera filter')
    gateway=val('gateway','all')
    if gateway!='all' and not re.fullmatch('[a-f0-9]{16}',gateway):raise ValueError('Invalid gateway filter')
    bounds='lat BETWEEN ? AND ? AND '+('lon BETWEEN ? AND ?' if west<=east else '(lon>=? OR lon<=?)')
    params=[south,north,west,east]
    with closing(db(path)) as connection:
        sites=[dict(row) for row in connection.execute('SELECT * FROM gateways ORDER BY label')]
        meta=get_meta(connection,'cameras',{'phase':'pending','count':0,'attribution':ATTRIBUTION})
        coverage=get_meta(connection,'coverage',{'phase':'pending','meaning':'No fixed-gateway reception evidence synced yet.'})
        fixed=get_meta(connection,'fixed-gateways',[])
        trusted=' AND r.gateway IN ('+','.join('?' for _ in fixed)+')' if fixed else ' AND 0'
        result={'cameras':[],'receptions':[],'gateways':sites,'cameraStatus':meta,'coverageStatus':coverage,
            'worker':get_meta(connection,'worker',{}),'homeStatus':get_meta(connection,'home-status',{}),'attribution':ATTRIBUTION,'nearest':[]}
        filter_sql=" AND lower(brand) LIKE '%flock%'" if manufacturer=='flock' else ''
        if 'cameras' in enabled:
            if zoom<12:
                cell=360/(2**zoom*4)
                rows=connection.execute('SELECT AVG(lat) lat,AVG(lon) lon,COUNT(*) count FROM cameras WHERE '+bounds+filter_sql+' GROUP BY CAST((lat+90)/? AS INT),CAST((lon+180)/? AS INT) LIMIT 1501',params+[cell,cell]).fetchall()
                result['cameras']=[dict(row,cluster=True) for row in rows[:1500]];result['cameraTruncated']=len(rows)>1500
            else:
                rows=connection.execute('SELECT * FROM cameras WHERE '+bounds+filter_sql+' ORDER BY id LIMIT 1501',params).fetchall()
                result['cameras']=[dict(row) for row in rows[:1500]];result['cameraTruncated']=len(rows)>1500
        if 'coverage' in enabled:
            gate_sql=' AND r.gateway=?' if gateway!='all' else ''
            rows=connection.execute('''SELECT r.* FROM receptions r JOIN gateways g ON g.id=r.gateway
                WHERE g.role='fixed' AND g.hidden=0 AND '''+bounds.replace('lat','r.lat').replace('lon','r.lon')+gate_sql+trusted+' ORDER BY received DESC LIMIT 2001',params+([gateway] if gateway!='all' else [])+fixed).fetchall()
            result['receptions']=[dict(row) for row in rows[:2000]];result['coverageTruncated']=len(rows)>2000
        result['coverageStatus']['count']=connection.execute("SELECT COUNT(*) FROM receptions r JOIN gateways g ON g.id=r.gateway WHERE g.role='fixed' AND g.hidden=0"+trusted,fixed).fetchone()[0]
        if 'alertLat' in options or 'alertLon' in options:
            lat,lon=number(float(val('alertLat','nan')),-90,90),number(float(val('alertLon','nan')),-180,180)
            radius=number(float(val('alertMeters',300)),50,5000)
            degrees=radius/111000;wide=min(180,degrees/max(.01,math.cos(math.radians(lat))))
            candidates=connection.execute('SELECT id,label,lat,lon,brand FROM cameras WHERE lat BETWEEN ? AND ?'+filter_sql,(max(-90,lat-degrees),min(90,lat+degrees))).fetchall()
            near=[]
            for row in candidates:
                if abs((row['lon']-lon+180)%360-180)>wide:continue
                meters=distance(lat,lon,row['lat'],row['lon'])
                if meters<=radius:near.append(dict(row,meters=round(meters)))
            result['nearest']=sorted(near,key=lambda row:row['meters'])[:5]
        return result


def tick(path,config,now=None):
    now=time.time() if now is None else now
    with closing(db(path)) as connection:
        camera=get_meta(connection,'cameras',{});last=get_meta(connection,'camera-attempt',0)
        requested=get_meta(connection,'refresh-request',0);completed=get_meta(connection,'refresh-completed',0)
        home_last=get_meta(connection,'home-attempt',0);cursor=get_meta(connection,'coverage-cursor',0);instance=get_meta(connection,'coverage-instance')
    manual=requested>completed
    if config.get('map_camera_download',True) and (manual or camera.get('source')!='Local import') and now-last> (60 if manual else 3600) and (manual or now-camera.get('fetchedAt',0)>86400):
        with closing(db(path)) as connection,connection:put_meta(connection,'camera-attempt',now)
        try:
            with build_opener(NoRedirect()).open(Request(CAMERA_URL,headers={'User-Agent':'CarAssistantMap/1.0 (+https://github.com/TheRiddler08/Ford-Better-Head-Unit-)'}),timeout=60) as response:
                raw=response.read(8*1024**2+1)
            if len(raw)>8*1024**2:raise ValueError('Camera download exceeds limit')
            raw_hash=hashlib.sha256(raw).hexdigest()
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as zipped:raw=zipped.read(LIMIT+1)
            if len(raw)>LIMIT:raise ValueError('Camera export exceeds limit')
            import_cameras(path,json.loads(raw),'DeFlock / FlockHopper USA export',now)
            with closing(db(path)) as connection,connection:
                bundle=get_meta(connection,'bundled-snapshot',{})
                if raw_hash==bundle.get('sha256'):
                    value=get_meta(connection,'cameras');value.update(sourceDate=bundle.get('sourceDate'),snapshotRevision=bundle.get('revision'));put_meta(connection,'cameras',value)
            phase='cached'
        except (OSError,ValueError,EOFError):phase='download-unavailable-cache-retained'
        with closing(db(path)) as connection,connection:put_meta(connection,'worker',{'phase':phase,'checkedAt':now})
    if now-home_last>60 or manual:
        with closing(db(path)) as connection,connection:put_meta(connection,'home-attempt',now)
        try:
            from control.update_agent import Agent
            agent=Agent(config)
            for _ in range(4):
                data=agent.call('/api/device/map-coverage?after='+str(cursor))
                if cursor and instance!=data.get('instance'):data=agent.call('/api/device/map-coverage?after=0')
                import_coverage(path,data);cursor=data['next'];instance=data['instance']
                with closing(db(path)) as connection,connection:put_meta(connection,'home-status',{'phase':'synced','checkedAt':now})
                if not data.get('hasMore'):break
        except (OSError,ValueError,KeyError):
            with closing(db(path)) as connection,connection:put_meta(connection,'home-status',{'phase':'home-unavailable-cache-retained','checkedAt':now})
    if manual:
        with closing(db(path)) as connection,connection:put_meta(connection,'refresh-completed',requested)


def main():
    while True:
        try:
            config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text())
            if config.get('role')=='tiny' and config.get('enabled',True) and config.get('map_layers',True):
                override=Path('/etc/car-dashboard/update-link.json')
                if override.exists():config.update(json.loads(override.read_text()))
                path=location(config['home'])
                try:seed_snapshot(path,config['repo'])
                except (OSError,ValueError):pass
                tick(path,config)
        except (OSError,ValueError,sqlite3.Error):pass
        time.sleep(15)


if __name__=='__main__':main()
