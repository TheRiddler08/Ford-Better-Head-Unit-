"""Optional disposable Docker test of the real pinned OSRM preprocessing/API."""
import json,os,subprocess,sys,tempfile,time,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dev.setup_us_routing import IMAGE

env=dict(os.environ)
for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):env.pop(key,None)
def docker(*args,check=True):
    result=subprocess.run(['docker','--host=unix:///var/run/docker.sock',*map(str,args)],env=env,capture_output=True,text=True)
    if check and result.returncode:raise RuntimeError(result.stdout[-1500:]+result.stderr[-1500:])
    return result

with tempfile.TemporaryDirectory(prefix='car-osrm-smoke-') as directory:
    root=Path(directory)
    (root/'tiny.osm').write_text('''<?xml version="1.0"?><osm version="0.6" generator="test"><node id="1" lat="40.0" lon="-100.0" version="1"/><node id="2" lat="40.0" lon="-99.99" version="1"/><node id="3" lat="40.01" lon="-99.99" version="1"/><way id="1" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="residential"/><tag k="name" v="Fixture Road"/></way></osm>''')
    options=['--network','none','--user',f'{os.getuid()}:{os.getgid()}','--cap-drop','ALL','--security-opt','no-new-privileges']
    for command in (['osrm-extract','-p','/opt/car.lua','/data/tiny.osm'],['osrm-partition','/data/tiny.osrm'],['osrm-customize','/data/tiny.osrm']):
        docker('run','--rm',*options,'-v',str(root)+':/data',IMAGE,*command)
    name='car-routing-smoke-'+uuid.uuid4().hex[:10]
    try:
        docker('run','-d','--name',name,*options,'-v',str(root)+':/data:ro',IMAGE,'osrm-routed','--algorithm','mld','--mmap=true','/data/tiny.osrm')
        deadline=time.monotonic()+20
        url='http://127.0.0.1:5000/route/v1/driving/-100,40;-99.99,40.01?overview=full&geometries=geojson&steps=true&radiuses=1000;1000'
        while True:
            response=docker('exec',name,'wget','-Y','off','-q','-O','-',url,check=False)
            if response.returncode==0:break
            if time.monotonic()>deadline:raise RuntimeError('OSRM API not ready: '+response.stderr[-1000:]+response.stdout[-1000:]+docker('logs',name).stdout[-1500:])
            time.sleep(.2)
        result=json.loads(response.stdout)
        assert result['code']=='Ok' and result['routes'][0]['distance']>0
        assert result['routes'][0]['geometry']['type']=='LineString'
        assert len(result['routes'][0]['legs'][0]['steps'])>=2
    finally:docker('rm','-f',name,check=False)
print('Real OSRM v6 extraction, customization, mmap startup and road route/steps API passed')
