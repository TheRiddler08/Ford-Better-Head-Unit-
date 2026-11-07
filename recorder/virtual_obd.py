#!/usr/bin/env python3
"""Separate ELM327-compatible serial source for the unchanged python-OBD recorder."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import pty
import pwd
import select
import signal
import subprocess
import tempfile
import time
import tty

DEFAULTS={'speed':0,'rpm':800,'coolant':85,'voltage':13.8,'fuel':70,'load':20,'throttle':0,'intake':25}
LIMITS={'speed':(0,255),'rpm':(0,16383.75),'coolant':(-40,215),'voltage':(0,65.535),'fuel':(0,100),'load':(0,100),'throttle':(0,100),'intake':(-40,215)}
PIDS={1,4,5,12,13,15,17,28,32,47,64,66}


def values(path):
    data=dict(DEFAULTS)
    if path:
        path=Path(path)
        if path.is_symlink() or path.stat().st_size>8192:raise ValueError('Use a regular JSON values file under 8 KB')
        proposed=json.loads(path.read_text())
        if not isinstance(proposed,dict) or set(proposed)-set(DEFAULTS):raise ValueError('Unknown OBD value')
        data.update(proposed)
    for key,value in data.items():
        low,high=LIMITS[key]
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not low<=value<=high:raise ValueError('Invalid '+key)
    return data


class Adapter:
    def __init__(self, read_values):
        self.read_values=read_values
        self.echo=True;self.headers=False;self.spaces=True
    def command(self, raw):
        command=raw.strip().upper().replace(' ','')
        if not command:return b'\r>'
        data=self.read_values()
        result='OK'
        if command in ('ATZ','ATWS','ATD'):
            self.echo=True;self.headers=False;self.spaces=True
            result='ELM327 v1.5' if command!='ATD' else 'OK'
        elif command=='ATI':result='ELM327 v1.5'
        elif command=='ATE0':self.echo=False
        elif command=='ATE1':self.echo=True
        elif command=='ATH0':self.headers=False
        elif command=='ATH1':self.headers=True
        elif command=='ATS0':self.spaces=False
        elif command=='ATS1':self.spaces=True
        elif command=='ATDPN':result='A6'
        elif command=='ATDP':result='ISO 15765-4 (CAN 11/500)'
        elif command=='ATRV':result=f"{data['voltage']:.1f}V"
        elif command=='ATIGN':result='ON'
        elif command.startswith('AT'):result='OK'
        elif command.startswith('01') and len(command) in (4,5):
            try:pid=int(command[2:4],16)
            except ValueError:pid=-1
            payload=None
            if pid in (0,32,64):
                bitmap=sum(1<<(32-(p-pid)) for p in PIDS if pid<p<=pid+32)
                payload=list(bitmap.to_bytes(4,'big'))
            elif pid==1:payload=[0,7,0,0]
            elif pid==4:payload=[round(data['load']*255/100)]
            elif pid==5:payload=[round(data['coolant']+40)]
            elif pid==12:payload=list(round(data['rpm']*4).to_bytes(2,'big'))
            elif pid==13:payload=[round(data['speed'])]
            elif pid==15:payload=[round(data['intake']+40)]
            elif pid==17:payload=[round(data['throttle']*255/100)]
            elif pid==28:payload=[6]
            elif pid==47:payload=[round(data['fuel']*255/100)]
            elif pid==66:payload=list(round(data['voltage']*1000).to_bytes(2,'big'))
            if payload is None:result='NO DATA'
            else:
                parts=[f'{n:02X}' for n in [0x41,pid,*payload]]
                if self.headers:parts=['7E8',f'{len(parts):02X}',*parts]
                result=(' ' if self.spaces else '').join(parts)
        elif command in ('03','07'):result='7E8 02 '+('43' if command=='03' else '47')+' 00' if self.headers else ('43 00' if command=='03' else '47 00')
        else:result='NO DATA'
        return ((command+'\r' if self.echo else '')+result+'\r>').encode('ascii')


@contextmanager
def recorder_source(device, replace):
    if not replace:
        yield
        return
    if os.geteuid()!=0:raise RuntimeError('System recorder replacement requires sudo')
    unit='car-obd-monitor.service'
    was_active=subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
    user=subprocess.check_output(['systemctl','show','-p','User','--value',unit],text=True).strip()
    if not user:raise RuntimeError('Expected an installed OBD recorder with a normal service user')
    account=pwd.getpwnam(user)
    os.chown(device,account.pw_uid,account.pw_gid);os.chmod(device,0o600)
    directory=Path('/run/systemd/system')/(unit+'.d')
    override=directory/'90-car-virtual-obd.conf'
    if override.exists() or override.is_symlink():raise RuntimeError('An OBD source override already exists; inspect it first')
    directory.mkdir(exist_ok=True)
    try:
        subprocess.run(['systemctl','stop',unit],check=True)
        with override.open('x') as stream:stream.write('[Service]\nEnvironment=OBD_PORT='+device+'\n')
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','start',unit],check=True)
        yield
    finally:
        subprocess.run(['systemctl','stop',unit],check=True)
        override.unlink(missing_ok=True)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        if was_active:subprocess.run(['systemctl','start',unit],check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--values',help='JSON file of manual values; reread for every request')
    parser.add_argument('--seconds',type=float,default=1800)
    parser.add_argument('--replace-system-obd',action='store_true')
    args=parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds<=0:parser.error('Duration must be positive')
    try:values(args.values)
    except (ValueError,OSError) as exc:parser.error(str(exc))
    stop=False
    def terminate(*_):
        nonlocal stop
        stop=True
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,terminate)
    master,slave=pty.openpty();tty.setraw(slave)
    lock=None
    try:
        if args.replace_system_obd:
            fd=os.open('/run/lock/car-virtual-obd.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            lock=os.fdopen(fd,'w')
            if os.fstat(fd).st_uid!=0:raise RuntimeError('OBD source lock is not root-owned')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        device=os.ttyname(slave)
        print('ELM327 serial device: '+device,flush=True)
        adapter=Adapter(lambda:values(args.values))
        with recorder_source(device,args.replace_system_obd):
            print('External OBD source active; normal recorder/history consumers receive these values. Ctrl+C restores the prior source.',flush=True)
            started=time.monotonic();buffer=b''
            while not stop and time.monotonic()-started<args.seconds:
                ready,_,_=select.select([master],[],[],.2)
                if not ready:continue
                buffer+=os.read(master,4096)
                if len(buffer)>8192:buffer=b''
                while b'\r' in buffer:
                    line,buffer=buffer.split(b'\r',1)
                    try:response=adapter.command(line.decode('ascii',errors='ignore'))
                    except (ValueError,OSError):response=b'NO DATA\r>'
                    os.write(master,response)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:parser.exit(1,str(exc)+'\n')
    finally:
        os.close(master);os.close(slave)
        if lock:lock.close()


if __name__=='__main__':main()
