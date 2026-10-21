"""Optional ChirpStack v4 MQTT home gateway adapter.

Install 'paho-mqtt' on the home server. This adapter does NOT replace a
LoRaWAN network server or end-device modem firmware; it enqueues one
downlink per approved uplink (Class A) or interval (Class C). The network
server performs actual regional radio scheduling.
"""
import base64
import json
import os
import time
from collections import defaultdict

from control.home_server import (ROOT, ack_revision, export_next,
                                 mark_transmitted, radio_key, rewind_chunks)
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
        self.assembler=Assembler(radio_key())
        self.last=0.0
        self.day=int(time.time()//86400)
        self.sent=0

    def may_send(self):
        day=int(time.time()//86400)
        if day!=self.day:self.sent=0;self.day=day
        return self.sent<DAILY_LIMIT and time.monotonic()-self.last >= COOLDOWN

    def send_one(self):
        if not self.may_send():return
        item=export_next(NAME)
        if not item:return
        frame=bytes.fromhex(item["frame_hex"])
        topic=f"application/{APP}/device/{DEV_EUI}/command/down"
        message={"confirmed":False,"fPort":FPORT,"data":base64.b64encode(frame).decode("ascii")}
        info=self.client.publish(topic,json.dumps(message,separators=(",",":")),qos=1)
        if info.rc!=0:
            print("Broker rejected publish; keeping fragment queued",flush=True)
            return
        # This acknowledges broker queueing, NOT successful over-air delivery.
        if mark_transmitted(NAME,item["revision"],item["index"]):
            self.last=time.monotonic();self.sent+=1
            print(f"Queued fragment {item['index']+1} of revision {item['revision']} "
                  f"({len(frame)} bytes; {self.sent}/{DAILY_LIMIT} daily cap)",flush=True)

    def on_connect(self,client,userdata,flags,reason_code,properties=None):
        topic=f"application/{APP}/device/{DEV_EUI}/event/up"
        client.subscribe(topic,qos=1)
        print("Subscribed to "+topic,flush=True)

    def on_message(self,client,userdata,msg):
        try:
            wire=json.loads(msg.payload)
            payload=base64.b64decode(wire.get("data",""),validate=True)
            if len(payload)<=46 and payload:
                result=self.assembler.accept(payload)
                if result:
                    packet=result["payload"]
                    if result["kind"]=="ack" and isinstance(packet,dict) and packet.get("applied") is True:
                        ack_revision(NAME,packet["revision"])
                    elif result["kind"]=="chunk-request" and isinstance(packet,dict):
                        rewind_chunks(NAME,packet.get("sequence"),packet.get("missing"))
            if MODE=="A":self.send_one()
        except (ValueError,TypeError,KeyError) as exc:
            print("Ignored invalid uplink: "+str(exc),flush=True)

    def run(self):
        self.client.on_connect=self.on_connect
        self.client.on_message=self.on_message
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
    if not APP or not DEV_EUI or len(DEV_EUI)!=16:
        raise SystemExit("Set ChirpStack application ID and 16-character DevEUI")
    if MODE not in ("A","C"):
        raise SystemExit("CAR_LORAWAN_CLASS must be A or C")
    from paho.mqtt.client import Client, CallbackAPIVersion
    mqtt=Client(callback_api_version=CallbackAPIVersion.VERSION2)
    if USERNAME:
        mqtt.username_pw_set(USERNAME,PASSWORD)
    if os.getenv("CAR_CHIRPSTACK_MQTT_TLS","0")=="1":
        mqtt.tls_set()
    Bridge(mqtt).run()

if __name__=="__main__":
    main()
