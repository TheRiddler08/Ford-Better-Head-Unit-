"""Home Home Assistant MQTT discovery and states, separate from the car HA UI.

Reads only authenticated/decrypted telemetry and the signed update journal.
MQTT commands are two fixed actions: request read-only health, or pause/resume
home radio update delivery. No raw radio frames, shell commands or keys accepted.
"""
import json
import os
from pathlib import Path
import re
import time
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
    for metric,label in (('pending','Pending updates'),('failed','Failed updates'),('phase','Update delivery state'),('transport','Last update transport')):
        key=ident+'_updates_'+metric
        result['homeassistant/sensor/'+key+'/config']={'name':label,'unique_id':key,'device':device,
          'state_topic':base+'/updates','value_template':"{{ value_json."+metric+" }}",
          'availability_topic':'car/'+identity(car)+'/bridge/availability'}
    key=ident+'_health_request'
    result['homeassistant/button/'+key+'/config']={'name':'Request health report','unique_id':key,'device':device,
          'command_topic':'car/'+identity(car)+'/command/'+role+'/status','payload_press':'PRESS',
          'availability_topic':'car/'+identity(car)+'/bridge/availability'}
    for metric,label in (('pending','Pending data files'),('conflicts','Data sync conflicts'),('phase','Data sync state')):
        key=ident+'_sync_'+metric
        result['homeassistant/sensor/'+key+'/config']={'name':label,'unique_id':key,'device':device,
          'state_topic':base+'/sync','value_template':"{{ value_json."+metric+" }}",
          'availability_topic':'car/'+identity(car)+'/bridge/availability'}
        if metric=='phase':result['homeassistant/sensor/'+key+'/config']['json_attributes_topic']=base+'/sync'
    return result


def update_states(rows):
    states={role:{'pending':0,'failed':0,'phase':'idle','transport':'none','acknowledgedParts':0,'totalParts':0} for role in ROLES}
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
                         transport=row.get('lastTransport') or 'none',acknowledgedParts=row.get('acknowledged',0),totalParts=row.get('total',0))
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


class HomeHA:
    def __init__(self,client,root,car='car-1'):
        self.client=client;self.root=Path(root);self.car=identity(car);self.previous={};self.connected=False
        self.base='car/'+self.car;self.status=self.root/'home-ha-status.json'

    def publish(self,topic,value,force=False):
        raw=json.dumps(value,separators=(',',':'),allow_nan=False) if not isinstance(value,str) else value
        if not force and self.previous.get(topic)==raw:return
        info=self.client.publish(topic,raw,qos=1,retain=True)
        if info.rc!=0:raise OSError('Home Assistant MQTT publish rejected')
        self.previous[topic]=raw

    def on_connect(self,client,userdata,flags,reason,properties=None):
        if reason!=0:return
        self.connected=True;self.previous={}
        self.publish(self.base+'/bridge/availability','offline',True)
        for role in ROLES:
            for topic,payload in discovery(self.car,role).items():self.publish(topic,payload,True)
        self.publish('homeassistant/switch/'+self.car+'_radio_updates/config',{
          'name':'Car radio update delivery','unique_id':self.car+'_radio_updates',
          'command_topic':self.base+'/command/update_delivery','state_topic':self.base+'/update_delivery',
          'availability_topic':self.base+'/bridge/availability'})
        client.subscribe(self.base+'/command/+/status',qos=1)
        client.subscribe(self.base+'/command/update_delivery',qos=1)
        self.tick() # Replace stale retained states before advertising availability.
        self.publish(self.base+'/bridge/availability','online',True)
        atomic(self.status,b'{"phase":"mqtt-connected","target":"home-home-assistant"}')

    def on_disconnect(self,*_):
        self.connected=False;atomic(self.status,b'{"phase":"mqtt-disconnected-retrying"}')

    def on_message(self,client,userdata,message):
        try:self.command(message)
        except (OSError,ValueError,TypeError) as error:
            atomic(self.status,json.dumps({'phase':'command-rejected','detail':type(error).__name__}).encode())

    def command(self,message):
        if getattr(message,'retain',False):return # Never replay old retained commands on reconnect.
        if message.topic==self.base+'/command/update_delivery':
            marker=self.root/'radio/update-delivery-paused'
            if message.payload==b'OFF':atomic(marker,b'paused')
            elif message.payload==b'ON':marker.unlink(missing_ok=True)
            return
        for role in ROLES:
            if message.topic==self.base+'/command/'+role+'/status' and message.payload==b'PRESS':
                from control.software import Software
                from control.event_transfer import Events
                from control.messages import enqueue
                enqueue(Events(self.root/'radio/catalog.sqlite3',Software(self.root/'software').key(),destination='home'),
                        'home',role,None,action='status-request')

    def tick(self):
        if not self.connected:return
        from control.software import Software
        service=Software(self.root/'software')
        sensors=snapshot(self.root/'radio/sensors.sqlite3');updates=journal_states(service.journal)
        for role in ROLES:
            self.publish(self.base+'/'+role+'/state',sensors[role])
            self.publish(self.base+'/'+role+'/updates',updates[role])
            from control.file_sync import home_files
            files=home_files(self.root,role,service.key());rows=files.status()
            selected=[row for row in rows if row['name'] in files.selected]
            conflicts=sum(row['phase']=='conflict' for row in selected);pending=sum(row['id'] is not None for row in selected)
            self.publish(self.base+'/'+role+'/sync',{'pending':pending,'conflicts':conflicts,'files':rows,
                'phase':'not-configured' if not files.selected else 'conflict' if conflicts else 'pending' if pending else 'last-transfer-applied' if rows else 'idle'})
        self.publish(self.base+'/update_delivery','OFF' if (self.root/'radio/update-delivery-paused').exists() else 'ON')


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
    client.on_connect=worker.on_connect;client.on_disconnect=worker.on_disconnect;client.on_message=worker.on_message
    client.connect(host,int(os.getenv('CAR_HA_MQTT_PORT','1883')),keepalive=60);client.loop_start()
    try:
        while True:
            worker.tick();time.sleep(10)
    finally:
        try:
            if worker.connected:worker.publish(worker.base+'/bridge/availability','offline',True)
        finally:client.disconnect();client.loop_stop()


if __name__=='__main__':main()
