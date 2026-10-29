"""Read-only normalized snapshots for Home Assistant; never touches the adapter."""
from datetime import datetime
import math
import time

# key, recorder name, canonical unit
METRICS = {
 'speed':('SPEED','km/h'), 'rpm':('RPM','rpm'), 'coolant':('COOLANT_TEMP','°C'),
 'voltage':('CONTROL_MODULE_VOLTAGE','V'), 'fuel':('FUEL_LEVEL','%'),
 'load':('ENGINE_LOAD','%'), 'throttle':('THROTTLE_POS','%'), 'intake':('INTAKE_TEMP','°C')}


def normalized(value, unit, expected):
    if isinstance(value,bool):return None
    try:value=float(value)
    except (TypeError,ValueError,OverflowError):return None
    if not math.isfinite(value):return None
    unit=str(unit or '').lower().replace(' ','_')
    accepted={'km/h':{'km/h','kph','kilometer_per_hour','kilometers_per_hour'},
              'rpm':{'rpm','revolutions_per_minute','revolution_per_minute'},
              '°C':{'°c','degc','celsius','degree_celsius'},'V':{'v','volt','volts'},
              '%':{'%','percent','percentage'}}
    if unit in accepted[expected]:return value
    if expected=='km/h' and unit in ('mph','mile_per_hour','miles_per_hour'):return value*1.609344
    if expected=='°C' and unit in ('°f','degf','fahrenheit','degree_fahrenheit'):return (value-32)*5/9
    return None


def snapshot(sensors, gps, now=None):
    now=time.time() if now is None else now
    metrics={}
    for key,(name,unit) in METRICS.items():
        row=sensors.get(name,{})
        value=normalized(row.get('number'),row.get('unit'),unit)
        try:
            stamp=float(row.get('ts'))
            if not math.isfinite(stamp):stamp=None
            age=now-stamp if stamp is not None else math.inf
        except (TypeError,ValueError):stamp=None;age=math.inf
        fresh=value is not None and -20<=age<=45
        metrics[key]={'value':value,'fresh':fresh,'unit':unit,'lastSeen':stamp}
    position=None
    if isinstance(gps,dict) and gps.get('fix'):
        try:
            if isinstance(gps.get('lat'),bool) or isinstance(gps.get('lon'),bool):raise ValueError('Invalid coordinates')
            lat,lon=float(gps['lat']),float(gps['lon'])
            fresh=math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180
            if gps.get('time'):
                stamp=datetime.fromisoformat(str(gps['time']).replace('Z','+00:00'))
                fresh=fresh and stamp.tzinfo is not None and -20<=now-stamp.timestamp()<=45
            if fresh:position={'lat':lat,'lon':lon,'time':gps.get('time'),'mode':gps.get('mode')}
        except (ValueError,TypeError,KeyError,OverflowError):pass
    return {'schema':1,'metrics':metrics,'obdFresh':any(metrics[k]['fresh'] for k in ('speed','rpm')),
            'gps':position,'serverTime':now}
