"""Home Home Assistant MQTT discovery and states, separate from the car HA UI.

Reads only authenticated/decrypted telemetry and the signed update journal.
MQTT commands are two fixed actions: request read-only health, or pause/resume
home radio update delivery. No raw radio frames, shell commands or keys accepted.
"""
import json
import os
from pathlib import Path
from queue import Queue,Empty,Full
import re
import sqlite3
import time
from types import SimpleNamespace
from control.telemetry import METRICS,ROLES,snapshot
from control.update_agent import atomic


def identity(car):
    if not isinstance(car,str) or not re.fullmatch('[A-Za-z0-9_-]{1,48}',car):raise ValueError('Invalid car identifier')
    return car.replace('-','_')


def discovery(car,role):
    if role not in ROLES:raise ValueError('Unknown car role')
    ident=identity(car)+'_'+role;base='car/'+identity(car)+'/'+role
    device={'identifiers':[ident],'name':'Car '+role.title(),'manufacturer':'Car network','model':'LoRaWAN vehicle integration'}
    result={}
    for metric,(_,_,unit) in METRICS.items():
        key=ident+'_'+metric
        result['homeassistant/sensor/'+key+'/config']={
          'name':'Last reported '+metric.replace('_',' '),'unique_id':key,'device':device,
          'state_topic':base+'/state','value_template':"{{ value_json.values.get('"+metric+"') }}",
          'json_attributes_topic':base+'/state',
          'json_attributes_template':'{{ {"observed_at_epoch": value_json.observed.get("'+metric+'")} | to_json }}',
          'unit_of_measurement':unit,'expire_after':1800,
          'availability':[{'topic':'car/'+identity(car)+'/bridge/availability'},
             {'topic':base+'/state','value_template':"{{ 'online' if value_json.values.get('"+metric+"') is not none else 'offline' }}"}],
          'availability_mode':'all'}
        result['homeassistant/sensor/'+key+'/config']['state_class']='measurement'
        classes={'speed':'speed','coolant':'temperature','intake':'temperature','voltage':'voltage','uptime':'duration','free_space':'data_size'}
        if metric in classes:result['homeassistant/sensor/'+key+'/config']['device_class']=classes[metric]
    for metric,label in (('pending','Pending updates'),('failed','Failed updates'),('phase','Update delivery state'),('transport','Last update transport'),('progress','Update fragments received')):
        key=ident+'_updates_'+metric
        result['homeassistant/sensor/'+key+'/config']={'name':label,'unique_id':key,'device':device,
          'state_topic':base+'/updates','value_template':"{{ value_json."+metric+" }}",
          'availability_topic':'car/'+identity(car)+'/bridge/availability'}
        if metric=='progress':result['homeassistant/sensor/'+key+'/config'].update(unit_of_measurement='%',entity_category='diagnostic')
        if metric=='phase':result['homeassistant/sensor/'+key+'/config']['json_attributes_topic']=base+'/updates'
    for metric,label in (('age','Latest observation age'),('transport','Last sensor transport')):
        key=ident+'_report_'+metric
        result['homeassistant/sensor/'+key+'/config']={'name':label,'unique_id':key,'device':device,
          'state_topic':base+'/report','value_template':"{{ value_json."+metric+" }}",'entity_category':'diagnostic',
          'availability':[{'topic':'car/'+identity(car)+'/bridge/availability'},
            {'topic':base+'/report','value_template':"{{ 'online' if value_json.observedAt is not none else 'offline' }}"}],
          'availability_mode':'all'}
        if metric=='age':result['homeassistant/sensor/'+key+'/config'].update(unit_of_measurement='s',device_class='duration')
    key=ident+'_report_fresh'
    result['homeassistant/binary_sensor/'+key+'/config']={'name':'Fresh sensor observations','unique_id':key,'device':device,
      'state_topic':base+'/report','value_template':"{{ 'ON' if value_json.fresh else 'OFF' }}",
      'json_attributes_topic':base+'/report','entity_category':'diagnostic',
      'availability_topic':'car/'+identity(car)+'/bridge/availability'}
    key=ident+'_health_request'
    result['homeassistant/button/'+key+'/config']={'name':'Request health report','unique_id':key,'device':device,
          'command_topic':'car/'+identity(car)+'/command/'+role+'/status','payload_press':'PRESS',
          'availability_topic':'car/'+identity(car)+'/bridge/availability'}
    for metric,label in (('pending','Pending data files'),('conflicts','Data sync conflicts'),('blocked','Blocked data files'),('phase','Data sync state')):
        key=ident+'_sync_'+metric
        result['homeassistant/sensor/'+key+'/config']={'name':label,'unique_id':key,'device':device,
          'state_topic':base+'/sync','value_template':"{{ value_json."+metric+" }}",
          'availability_topic':'car/'+identity(car)+'/bridge/availability'}
        if metric=='phase':result['homeassistant/sensor/'+key+'/config']['json_attributes_topic']=base+'/sync'
    return result


def update_states(rows):
    states={role:{'pending':0,'failed':0,'phase':'idle','transport':'none','acknowledgedParts':0,'totalParts':0,'progress':0} for role in ROLES}
    seen=set()
    for row in rows:
        role=row.get('role')
        if role not in states:continue
        state=states[role];phase=row['phase']
        state['pending']+=phase not in ('applied','failed');state['failed']+=phase=='failed'
        if role not in seen:
            progress=row.get('acknowledged',0);total=row.get('total',0)
            shown='awaiting-application' if phase=='queued' and total and progress>=total else 'receiving' if phase=='queued' and progress>0 else phase
            state.update(phase=shown,
                         transport=row.get('lastTransport') or 'none',acknowledgedParts=progress,totalParts=total,
                         progress=min(100,round(100*progress/total,1)) if total else 0)
            seen.add(role)
    return states


def journal_states(journal):
    """Count the full queue, rather than truncating it to the 32-row UI history."""
    states={}
    with journal.db() as db:
        for role in ROLES:
            rows=db.execute("SELECT phase,COUNT(*) FROM jobs WHERE direction='out' AND json_extract(doc,'$.role')=? GROUP BY phase",(role,)).fetchall()
            totals=dict(rows)
            row=db.execute("SELECT id,phase FROM jobs WHERE direction='out' AND json_extract(doc,'$.role')=? ORDER BY CASE WHEN phase IN ('applied','failed') THEN 1 ELSE 0 END,updated DESC LIMIT 1",(role,)).fetchone()
            values=[]
            if row:
                ident,phase=row
                received=db.execute('SELECT COUNT(*) FROM receipts WHERE id=?',(ident,)).fetchone()[0]
                total=db.execute('SELECT COUNT(*) FROM parts WHERE id=?',(ident,)).fetchone()[0]
                transport=(db.execute('SELECT transport FROM events WHERE id=? ORDER BY ts DESC LIMIT 1',(ident[:16],)).fetchone() or ['none'])[0]
                values=[{'role':role,'phase':phase,'acknowledged':received,'total':total,'lastTransport':transport}]
            state=update_states(values)[role]
            state['pending']=sum(count for phase,count in totals.items() if phase not in ('applied','failed'))
            state['failed']=totals.get('failed',0);states[role]=state
    return states


def sync_state(files):
    rows=[row for row in files.status() if row['name'] in files.selected]
    conflicts=sum(row['phase']=='conflict' for row in rows);pending=sum(row['id'] is not None for row in rows)
    blocked=sum(row['phase']=='blocked' for row in rows)
    return {'pending':pending,'conflicts':conflicts,'blocked':blocked,'files':rows,
        'phase':'not-configured' if not files.selected else 'blocked' if blocked else 'conflict' if conflicts else 'pending' if pending else 'last-transfer-applied' if rows else 'idle'}


def report_state(sample,now=None):
    now=time.time() if now is None else now
    observed=sample['observedAt'];age=max(0,int(now-observed)) if observed is not None else None
    return {'observedAt':observed,'receivedAt':sample['receivedAt'],'age':age,'transport':sample['transport'],
            'fresh':any(value is not None for value in sample['values'].values())}


def overview(root):
    """Read-only operator view; includes no credentials or file contents."""
    from control.software import Software
    from control.file_sync import home_files
    root=Path(root);service=Software(root/'software');now=time.time()
    sensors=snapshot(root/'radio/sensors.sqlite3',now=now);updates=journal_states(service.journal)
    path=root/'home-ha-status.json'
    status=json.loads(path.read_text()) if path.exists() else {'phase':'not-installed'}
    if status.get('phase')=='mqtt-connected' and not 0<=now-status.get('time',0)<=45:
        status=dict(status,phase='status-stale')
    roles={role:{'report':report_state(sensors[role],now),'updates':updates[role],
                 'sync':sync_state(home_files(root,role,service.key()))} for role in ROLES}
    return {'bridge':status,'roles':roles}


class HomeHA:
    def __init__(self,client,root,car='car-1'):
        self.client=client;self.root=Path(root);self.car=identity(car);self.previous={};self.connected=False
        self.base='car/'+self.car;self.status=self.root/'home-ha-status.json'
        self.rediscover=True;self.phase='mqtt-connecting';self.commands=Queue(maxsize=32);self.last_command=None
        self.last_discovery=None;self.last_publish=None

    def publish(self,topic,value,force=False):
        raw=json.dumps(value,separators=(',',':'),allow_nan=False) if not isinstance(value,str) else value
        if not force and self.previous.get(topic)==raw:return
        info=self.client.publish(topic,raw,qos=1,retain=True)
        if info.rc!=0:raise OSError('Home Assistant MQTT publish rejected')
        self.previous[topic]=raw;self.last_publish=time.time()

    def on_connect(self,client,userdata,flags,reason,properties=None):
        if reason!=0:
            self.connected=False;self.phase='mqtt-rejected';return
        self.connected=True;self.rediscover=True;self.phase='mqtt-discovering'
        client.subscribe('homeassistant/status',qos=1)
        client.subscribe(self.base+'/command/+/status',qos=1)
        client.subscribe(self.base+'/command/update_delivery',qos=1)

    def announce(self):
        self.previous={}
        self.publish(self.base+'/bridge/availability','offline',True)
        for role in ROLES:
            for topic,payload in discovery(self.car,role).items():self.publish(topic,payload,True)
        self.publish('homeassistant/switch/'+self.car+'_radio_updates/config',{
          'name':'Car radio update delivery','unique_id':self.car+'_radio_updates',
          'command_topic':self.base+'/command/update_delivery','state_topic':self.base+'/update_delivery',
          'availability_topic':self.base+'/bridge/availability'})

    def on_disconnect(self,*_):
        self.connected=False;self.phase='mqtt-disconnected-retrying'

    def on_connect_fail(self,*_):
        self.connected=False;self.phase='mqtt-disconnected-retrying'

    def on_message(self,client,userdata,message):
        # MQTT's network thread must not block on SQLite or filesystem work.
        if message.topic=='homeassistant/status':
            if message.payload==b'online':self.rediscover=True
            return
        if getattr(message,'retain',False):return
        if len(message.payload)>16:return
        try:self.commands.put_nowait(SimpleNamespace(topic=message.topic,payload=bytes(message.payload),retain=False))
        except Full:self.last_command={'phase':'queue-full','time':time.time()}

    def process_commands(self):
        for _ in range(32):
            try:message=self.commands.get_nowait()
            except Empty:break
            try:
                result=self.command(message)
                if result:self.last_command=dict(result,time=time.time())
            except (OSError,ValueError,TypeError,sqlite3.Error):
                self.last_command={'phase':'rejected','time':time.time()}

    def command(self,message):
        if getattr(message,'retain',False):return # Never replay old retained commands on reconnect.
        if message.topic==self.base+'/command/update_delivery':
            marker=self.root/'radio/update-delivery-paused'
            if message.payload==b'OFF':atomic(marker,b'paused')
            elif message.payload==b'ON':marker.unlink(missing_ok=True)
            else:return
            return {'phase':'applied','action':'update-delivery'}
        for role in ROLES:
            if message.topic==self.base+'/command/'+role+'/status' and message.payload==b'PRESS':
                cooldown=self.root/'radio'/('ha-health-'+role+'.json');now=time.time()
                previous=json.loads(cooldown.read_text()) if cooldown.exists() else {}
                if 0<=now-previous.get('queuedAt',0)<300:return {'phase':'rate-limited','action':'health','role':role}
                from control.software import Software
                from control.event_transfer import Events
                from control.messages import enqueue
                enqueue(Events(self.root/'radio/catalog.sqlite3',Software(self.root/'software').key(),destination='home'),
                        'home',role,None,action='status-request')
                atomic(cooldown,json.dumps({'queuedAt':now}).encode())
                return {'phase':'queued','action':'health','role':role}

    def write_status(self):
        atomic(self.status,json.dumps({'phase':self.phase,'target':'home-home-assistant','time':time.time(),
          'lastDiscovery':self.last_discovery,'lastPublishQueued':self.last_publish,'lastCommand':self.last_command}).encode())

    def tick(self):
        if not self.connected:self.write_status();return
        self.process_commands()
        refresh=self.rediscover
        if refresh:self.rediscover=False;self.announce()
        from control.software import Software
        service=Software(self.root/'software')
        sensors=snapshot(self.root/'radio/sensors.sqlite3');updates=journal_states(service.journal)
        for role in ROLES:
            self.publish(self.base+'/'+role+'/state',sensors[role])
            self.publish(self.base+'/'+role+'/report',report_state(sensors[role]))
            self.publish(self.base+'/'+role+'/updates',updates[role])
            from control.file_sync import home_files
            self.publish(self.base+'/'+role+'/sync',sync_state(home_files(self.root,role,service.key())))
        self.publish(self.base+'/update_delivery','OFF' if (self.root/'radio/update-delivery-paused').exists() else 'ON')
        if refresh:
            self.publish(self.base+'/bridge/availability','online',True);self.last_discovery=time.time()
        self.phase='mqtt-connected';self.write_status()

    def step(self):
        try:self.tick()
        except (OSError,ValueError,TypeError,sqlite3.Error):
            # Retry discovery atomically after an interrupted refresh; do not
            # kill paho's network loop or expose broker credentials in status.
            self.rediscover=True;self.previous={};self.phase='mqtt-retrying' if self.connected else 'mqtt-disconnected-retrying'
            self.write_status()


def main():
    from control.home_server import ROOT
    host=os.getenv('CAR_HA_MQTT_HOST','')
    if not host:
        while True:
            atomic(ROOT/'home-ha-status.json',b'{"phase":"unconfigured","detail":"Home Home Assistant MQTT broker required"}');time.sleep(30)
    if not re.fullmatch('[A-Za-z0-9_.:-]+',host):raise ValueError('CAR_HA_MQTT_HOST must be a hostname or IP, without URL credentials')
    if os.getenv('CAR_HA_MQTT_TLS','0') not in ('0','1'):raise ValueError('CAR_HA_MQTT_TLS must be 0 or 1')
    import paho.mqtt.client as mqtt
    client=mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2) if hasattr(mqtt,'CallbackAPIVersion') else mqtt.Client()
    worker=HomeHA(client,ROOT,os.getenv('CAR_CAR_ID','car-1'))
    client.will_set(worker.base+'/bridge/availability','offline',qos=1,retain=True)
    username=os.getenv('CAR_HA_MQTT_USER')
    if username:client.username_pw_set(username,os.getenv('CAR_HA_MQTT_PASSWORD',''))
    if os.getenv('CAR_HA_MQTT_TLS','0')=='1':client.tls_set(ca_certs=os.getenv('CAR_HA_MQTT_CA') or None)
    client.on_connect=worker.on_connect;client.on_disconnect=worker.on_disconnect;client.on_connect_fail=worker.on_connect_fail;client.on_message=worker.on_message
    port=int(os.getenv('CAR_HA_MQTT_PORT','1883'))
    if not 1<=port<=65535:raise ValueError('Invalid home MQTT port')
    client.reconnect_delay_set(min_delay=1,max_delay=60)
    client.connect_async(host,port,keepalive=60);client.loop_start()
    try:
        while True:
            worker.step();time.sleep(10)
    finally:
        try:
            if worker.connected:worker.publish(worker.base+'/bridge/availability','offline',True)
        finally:client.disconnect();client.loop_stop()


if __name__=='__main__':main()
