#!/usr/bin/env python3
"""Share the Duet's USB GPS and LoRaWAN modem with Tiny over the car switch.

Duet (`serve`): identifies hub serial devices by what they say, never by guessed
port names: a device already emitting NMEA is the GPS; a device answering the
read-only `AT+VER=?` with a RAK3172 RUI banner is the LoRaWAN modem. Each is
streamed raw on the car Ethernet address to Tiny's address only:
8800 status JSON, 8801 modem serial, 8802 GPS serial.

Tiny (`attach-gps`): adds the Duet GPS stream to the local gpsd so the existing
recorder, dashboard and HA continue unchanged. The modem stream is opened by the
normal radio adapter (`control.rak3172`), so radio keys and the public-key
encryption boundary stay on Tiny; only sealed frames cross the LAN.

No modem keys, join, band or GPS configuration commands are issued here.
"""
import argparse
import errno
import json
import os
from pathlib import Path
import re
import select
import socket
import subprocess
import termios
import time
import tty

DUET='172.16.42.1'
TINY='172.16.42.2'
STATUS_PORT,RADIO_PORT,GPS_PORT=8800,8801,8802
STATUS=Path('/run/car-dashboard/usb-share.json')
NMEA=re.compile(rb'\$(?:GP|GN|GL|GA|GB|BD)(?:RMC|GGA|GSA|GSV|VTG|GLL)')


def configure(fd,baud):
    tty.setraw(fd)
    attrs=termios.tcgetattr(fd)
    speed=getattr(termios,'B'+str(baud))
    attrs[4]=attrs[5]=speed
    termios.tcsetattr(fd,termios.TCSANOW,attrs)


def read_for(fd,seconds,limit=8192):
    data=b'';deadline=time.monotonic()+seconds
    while time.monotonic()<deadline and len(data)<limit:
        if select.select([fd],[],[],max(0,deadline-time.monotonic()))[0]:
            try:chunk=os.read(fd,4096)
            except BlockingIOError:continue
            if not chunk:break
            data+=chunk
    return data


def identify(path,opener=os.open):
    """Return ('gps'|'lorawan', baud) or None. Never sends configuration commands."""
    fd=opener(str(path),os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    try:
        for baud in (115200,9600):
            configure(fd,baud)
            termios.tcflush(fd,termios.TCIFLUSH)
            if NMEA.search(read_for(fd,2.5)):return ('gps',baud)
        configure(fd,115200)
        termios.tcflush(fd,termios.TCIFLUSH)
        os.write(fd,b'AT+VER=?\r\n')
        reply=read_for(fd,2)
        if b'RAK3172' in reply and b'RUI_' in reply:return ('lorawan',115200)
        return None
    finally:os.close(fd)


def candidates(root=Path('/dev')):
    found={}
    for link in sorted((root/'serial/by-id').glob('*')) if (root/'serial/by-id').is_dir() else []:
        try:found[str(link.resolve())]=link
        except OSError:pass
    for pattern in ('ttyACM*','ttyUSB*'):
        for path in sorted(root.glob(pattern)):found.setdefault(str(path),path)
    return found


class Device:
    def __init__(self,kind,path,baud,port):
        self.kind,self.path,self.baud,self.port=kind,path,baud,port
        self.fd=None;self.client=None;self.listener=None
    def open(self):
        self.fd=os.open(str(self.path),os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
        import fcntl
        try:fcntl.ioctl(self.fd,termios.TIOCEXCL)  # keep other probes off the shared device
        except OSError:pass
        configure(self.fd,self.baud)
    def close(self):
        for item in (self.client,self.listener):
            if item:
                try:item.close()
                except OSError:pass
        if self.fd is not None:
            try:os.close(self.fd)
            except OSError:pass
        self.fd=self.client=self.listener=None


def listen(port,address=DUET):
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind((address,port));sock.listen(2);sock.setblocking(False)
    return sock


def write_status(value):
    STATUS.parent.mkdir(parents=True,exist_ok=True)
    pending=STATUS.with_suffix('.tmp');pending.write_text(json.dumps(dict(value,updated=time.time())))
    os.replace(pending,STATUS)


def serve(address=DUET,peer=TINY,rescan=10,find=None,stop=None):
    find=find or candidates
    devices={};status_sock=None;last_scan=0;failed={}
    while not (stop and stop.is_set()):
        try:
            if status_sock is None:status_sock=listen(STATUS_PORT,address)
        except OSError:
            write_status({'phase':'waiting-car-ethernet','devices':{}});time.sleep(5);continue
        now=time.monotonic()
        if now-last_scan>=rescan:
            last_scan=now
            present=find()
            for kind,device in list(devices.items()):
                if str(device.path) not in present:device.close();del devices[kind]
            used={str(d.path) for d in devices.values()}
            for path in present:
                if path in used or now<failed.get(path,0) or len(devices)==2:continue
                try:result=identify(path)
                except OSError:result=None
                if not result or result[0] in devices:
                    failed[path]=now+60;continue
                kind,baud=result
                device=Device(kind,Path(path),baud,RADIO_PORT if kind=='lorawan' else GPS_PORT)
                try:device.open();device.listener=listen(device.port,address);devices[kind]=device
                except OSError:device.close();failed[path]=now+30
            write_status({'phase':'sharing' if devices else 'no-devices',
                          'devices':{k:{'path':str(d.path),'port':d.port,'client':d.client is not None} for k,d in devices.items()}})
        readers=[status_sock]+[s for d in devices.values() for s in (d.listener,d.client,d.fd) if s is not None]
        try:ready,_,_=select.select(readers,[],[],1)
        except (OSError,ValueError):ready=[]
        for item in ready:
            if item is status_sock:
                try:
                    conn,(host,_)=status_sock.accept()
                    with conn:
                        if host==peer:conn.sendall(json.dumps({'schema':1,'devices':sorted(devices)}).encode())
                except OSError:pass
                continue
            for kind,device in list(devices.items()):
                try:
                    if item is device.listener:
                        conn,(host,_)=device.listener.accept()
                        if host!=peer:conn.close();continue
                        if device.client:device.client.close()  # newest Tiny connection wins
                        conn.setblocking(False);device.client=conn
                    elif item is device.client:
                        data=device.client.recv(4096)
                        if not data:device.client.close();device.client=None
                        else:os.write(device.fd,data)
                    elif item is device.fd:
                        data=os.read(device.fd,4096)
                        if not data:raise OSError(errno.ENODEV,'unplugged')
                        if device.client:
                            try:device.client.sendall(data)
                            except OSError:device.client.close();device.client=None
                except BlockingIOError:pass
                except OSError:
                    if item is device.client:device.client=None
                    else:device.close();devices.pop(kind,None);last_scan=0
    for device in devices.values():device.close()
    if status_sock:status_sock.close()


def remote_devices(host=DUET,timeout=2,port=STATUS_PORT):
    try:
        with socket.create_connection((host,port),timeout=timeout) as sock:
            data=sock.recv(1024)
        value=json.loads(data)
        return set(value.get('devices',[])) if value.get('schema')==1 else set()
    except (OSError,ValueError,AttributeError):return set()


def attach_gps(host=DUET,interval=10,run=subprocess.run,once=False):
    """Tiny: keep the Duet GPS stream registered as a gpsd source."""
    source='tcp://%s:%d'%(host,GPS_PORT)
    while True:
        phase='duet-gps-absent'
        if 'gps' in remote_devices(host):
            result=run(['gpsdctl','add',source],capture_output=True,timeout=15)
            phase='attached' if result.returncode==0 else 'gpsd-rejected-source'
        try:
            Path('/run/car-dashboard').mkdir(parents=True,exist_ok=True)
            pending=Path('/run/car-dashboard/remote-gps.json.tmp')
            pending.write_text(json.dumps({'phase':phase,'source':source,'updated':time.time()}))
            os.replace(pending,'/run/car-dashboard/remote-gps.json')
        except OSError:pass
        if once:return phase
        time.sleep(interval)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['serve','attach-gps'])
    args=parser.parse_args()
    if args.action=='serve':serve()
    else:attach_gps()
