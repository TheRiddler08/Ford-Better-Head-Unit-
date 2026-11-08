"""Optional ChirpStack v4 MQTT home gateway adapter.

Install 'paho-mqtt' on the home server. This adapter does NOT replace a
LoRaWAN network server or end-device modem firmware; it enqueues one
downlink per approved uplink (Class A) or interval (Class C). The network
server performs actual regional radio scheduling.
"""
import base64
import json
import os
import re
import time
from collections import defaultdict

from control.home_server import (ROOT, ack_revision, export_next,
                                 mark_transmitted, radio_key, rewind_chunks, software)
from control.lorawan_protocol import Assembler

HOST = os.getenv("CAR_CHIRPSTACK_MQTT_HOST","127.0.0.1")
PORT = int(os.getenv("CAR_CHIRPSTACK_MQTT_PORT","1883"))
APP = os.getenv("CAR_CHIRPSTACK_APP_ID","")
DEV_EUI = os.getenv("CAR_CHIRPSTACK_DEV_EUI","").lower()
NAME = os.getenv("CAR_CAR_ID","car-1")
MODE = os.getenv("CAR_LORAWAN_CLASS","A").upper()
FPORT = int(os.getenv("CAR_LORAWAN_FPORT","10"))
COOLDOWN = max(30,int(os.getenv("CAR_DOWNLINK_SPACING","60")))
DAILY_LIMIT = min(200,max(1,int(os.getenv("CAR_DOWNLINKS_PER_DAY","60"))))
USERNAME = os.getenv("CAR_CHIRPSTACK_MQTT_USER","")
PASSWORD = os.getenv("CAR_CHIRPSTACK_MQTT_PASSWORD","")

class Bridge:
    def __init__(self, client):
        self.client=client
        self.software=software()
        self.assembler=Assembler(radio_key())
        self.last=0.0
        self.day=int(time.time()//86400)
        self.sent=0
        self.queue_slot=0

    def events(self):
        from control.event_transfer import Events
        return Events(ROOT/'radio/catalog.sqlite3',radio_key(),destination='home')

    def cipher(self):
        from control.radio_crypto import RadioBox
        return RadioBox(ROOT/'radio/private.key',ROOT/'radio/car.pub',ROOT/'radio/sealed.sqlite3',b'D',
                        int(os.getenv('CAR_RADIO_MAX_APP_BYTES','46')))

    def diagnostic(self):
        from control.radio_diagnostic import Responder
        return Responder(ROOT/'radio/diagnostic.json',radio_key())

    def may_send(self):
        day=int(time.time()//86400)
        if day!=self.day:self.sent=0;self.day=day
        return self.sent<DAILY_LIMIT and time.monotonic()-self.last >= COOLDOWN

    def send_one(self):
        if not self.may_send():return
        diagnostic=self.diagnostic().outgoing()
        event=self.events().outgoing(ack=True) if (ROOT/'radio/catalog.sqlite3').exists() else None
        text_frame=self.events().outgoing(kinds=['message']) if (ROOT/'radio/catalog.sqlite3').exists() else None
        file_frame=self.events().outgoing(kinds=['file','file-receipt']) if (ROOT/'radio/catalog.sqlite3').exists() else None
        paused=(ROOT/'radio/update-delivery-paused').exists()
        item=None if paused else export_next(NAME)
        modern=None
        if not paused:
            frames=self.software.outgoing(NAME,limit=1,mark=False)
            if frames:modern=bytes.fromhex(frames[0])
        candidates=[(kind,raw) for kind,raw in (
            ('message',text_frame),('file',file_frame),('modern',modern),
            ('legacy',bytes.fromhex(item['frame_hex']) if item else None),('diagnostic',diagnostic)) if raw]
        if event:kind,frame='event',event
        elif candidates:
            kind,frame=candidates[self.queue_slot%len(candidates)];self.queue_slot+=1
        else:return
        if not self.software.journal.budget('mqtt-downlinks',COOLDOWN,DAILY_LIMIT):return
        cipher=self.cipher()
        if not cipher.peer.exists():return # Never send application plaintext before enrollment.
        encrypted=cipher.next(frame)
        topic=f"application/{APP}/device/{DEV_EUI}/command/down"
        message={"confirmed":False,"fPort":FPORT,"data":base64.b64encode(encrypted).decode("ascii")}
        info=self.client.publish(topic,json.dumps(message,separators=(",",":")),qos=1)
        if info.rc!=0:
            print("Broker rejected publish; keeping fragment queued",flush=True)
            return
        self.last=time.monotonic();self.sent+=1
        if not cipher.sent(frame):return
        if kind=='diagnostic':return
        # This acknowledges broker queueing, NOT successful over-air delivery.
        if kind=='event':
            self.events().sent(frame,ack=True);return
        if kind=='message':
            self.events().sent(frame);return
        if kind=='file':
            self.events().sent(frame);return
        if kind=='modern':
            self.software.journal.mark_sent(modern,radio_key(),"lorawan")
            print("Queued resumable update fragment; awaiting durable device receipt",flush=True)
            return
        if mark_transmitted(NAME,item["revision"],item["index"]):
            print(f"Queued fragment {item['index']+1} of revision {item['revision']} "
                  f"({len(frame)} bytes; {self.sent}/{DAILY_LIMIT} daily cap)",flush=True)

    def on_connect(self,client,userdata,flags,reason_code,properties=None):
        from control.update_agent import atomic
        atomic(ROOT/'radio-status.json',json.dumps({'phase':'mqtt-connected-awaiting-uplink','time':time.time()}).encode())
        topic=f"application/{APP}/device/{DEV_EUI}/event/up"
        client.subscribe(topic,qos=1)
        print("Subscribed to "+topic,flush=True)

    def on_message(self,client,userdata,msg):
        window=False
        try:
            wire=json.loads(msg.payload)
            if not isinstance(wire,dict):raise ValueError('Invalid uplink event')
            window=True
            from control.update_agent import atomic
            atomic(ROOT/'radio-status.json',json.dumps({'phase':'uplink-received','time':time.time()}).encode())
            payload=self.cipher().accept(base64.b64decode(wire.get("data",""),validate=True))
            if payload is None:return
            if payload[:1]==b'\x04':
                self.diagnostic().receive(payload)
            elif payload[:1]==b'\x05':
                from control.telemetry import decode,store
                store(ROOT/'radio/sensors.sqlite3',decode(payload,radio_key()),transport='lorawan')
            elif payload[:1]==b'\x02':
                from control.update_transfer import decode
                _,short,*_=decode(payload,radio_key())
                with self.software.journal.db() as db:row=db.execute('SELECT doc FROM jobs WHERE short=?',(short,)).fetchone()
                if row:self.software.receipts(NAME,json.loads(row[0])['role'],[payload.hex()],transport='lorawan')
            elif payload[:2]==b'\x01\x06':
                received=self.events().receive(payload,with_id=True)
                sample=received['doc'] if received else None
                if sample:
                    from control.messages import status_reply
                    status_reply(self.events(),sample,'home',ROOT)
                    if sample.get('type') in ('file','file-receipt'):
                        from control.file_sync import home_files
                        home_files(ROOT,sample['source'],radio_key()).receive_available(sample,received['id'])
                if sample and sample.get('type')=='gps':
                    from control.coverage import ingest
                    registry_file=ROOT/'gateways.json'
                    registry=json.loads(registry_file.read_text()) if registry_file.exists() else {}
                    ingest(ROOT/'coverage.sqlite3',dict(wire,object={'carGpsV1':sample}),registry)
            elif len(payload)<=46 and payload:
                result=self.assembler.accept(payload)
                if result:
                    packet=result["payload"]
                    if result["kind"]=="ack" and isinstance(packet,dict) and packet.get("applied") is True:
                        ack_revision(NAME,packet["revision"])
                    elif result["kind"]=="chunk-request" and isinstance(packet,dict):
                        rewind_chunks(NAME,packet.get("sequence"),packet.get("missing"))
        except (ValueError,TypeError,KeyError,ImportError) as exc:
            print("Ignored invalid application data in uplink: "+str(exc),flush=True)
        finally:
            if window and MODE=="A":self.send_one()

    def on_disconnect(self,*args):
        from control.update_agent import atomic
        atomic(ROOT/'radio-status.json',json.dumps({'phase':'mqtt-disconnected-retrying','time':time.time()}).encode())

    def run(self):
        self.client.on_connect=self.on_connect
        self.client.on_message=self.on_message
        self.client.on_disconnect=self.on_disconnect
        self.client.connect(HOST,PORT,keepalive=60)
        if MODE=="A":
            self.client.loop_forever(retry_first_connection=True)
        else:
            self.client.loop_start()
            try:
                while True:
                    self.send_one()
                    time.sleep(5)
            finally:
                self.client.loop_stop()

def main():
    if not re.fullmatch('[A-Za-z0-9_-]{1,64}',APP) or not re.fullmatch('[0-9a-f]{16}',DEV_EUI):
        from control.update_agent import atomic
        while True:
            atomic(ROOT/'radio-status.json',b'{"phase":"unconfigured","detail":"ChirpStack application and DevEUI required"}')
            time.sleep(60)
    if MODE not in ("A","C"):
        raise SystemExit("CAR_LORAWAN_CLASS must be A or C")
    import paho.mqtt.client as library
    mqtt=library.Client(callback_api_version=library.CallbackAPIVersion.VERSION2) if hasattr(library,'CallbackAPIVersion') else library.Client()
    if USERNAME:
        mqtt.username_pw_set(USERNAME,PASSWORD)
    if os.getenv("CAR_CHIRPSTACK_MQTT_TLS","0")=="1":
        mqtt.tls_set()
    Bridge(mqtt).run()

if __name__=="__main__":
    main()
