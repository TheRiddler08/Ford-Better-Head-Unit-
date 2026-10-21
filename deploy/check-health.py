#!/usr/bin/env python3
"""Systemd health check for dashboard and readable SQLite data."""
import json
from urllib.request import ProxyHandler, build_opener
cfg = {'CAR_BIND':'127.0.0.1','CAR_PORT':'8787'}
with open('/etc/car-dashboard/dashboard.env', encoding='utf-8') as f:
    for line in f:
        if '=' in line and not line.lstrip().startswith('#'):
            k,v = line.strip().split('=', 1)
            if k in cfg: cfg[k]=v.strip().strip('"\'')
host = cfg['CAR_BIND']
if host in ('0.0.0.0','::'): host='127.0.0.1'
if ':' in host: host=f'[{host.strip("[]")}]'
url=f'http://{host}:{int(cfg["CAR_PORT"])}'
client=build_opener(ProxyHandler({}))
with client.open(url+'/api/health', timeout=3) as r:
    data=json.load(r)
    assert r.status==200 and data.get('ok') and data.get('databaseExists')
with client.open(url+'/api/summary', timeout=3) as r:
    data=json.load(r)
    assert r.status==200 and isinstance(data.get('totalReadings'),int)
print('Healthy')
