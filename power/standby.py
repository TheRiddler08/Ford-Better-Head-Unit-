#!/usr/bin/env python3
"""Managed boot standby and spare-laptop car-off deep sleep."""
import argparse
from contextlib import ExitStack
import fcntl
import json
import ipaddress
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if result.returncode:raise RuntimeError(' '.join(args)+': '+result.stderr.strip())
    return result.stdout


def suspend_command():
    if Path('/run/systemd/system').exists():return ['systemctl','--check-inhibitors=yes','suspend']
    if shutil.which('loginctl'):return ['loginctl','suspend']
    raise RuntimeError('Suspend requires systemd or elogind/loginctl; not available on this OS')


def validate(config):
    if config.get('schema')!=1 or config.get('role') not in ('duet','tiny','laptop'):raise ValueError('Invalid standby role/schema')
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}',config['interface']):raise ValueError('Invalid wake interface')
    if config.get('sleep_mode') not in ('current','deep'):raise ValueError('sleep_mode must be current or deep')
    ip=ipaddress.IPv4Address(config['pi_address'])
    if ip.is_unspecified or ip.is_multicast:raise ValueError('Use the actual Pi Ethernet IPv4 address')
    home=Path(config['home'])
    if not home.is_absolute() or '..' in home.parts or str(home)=='/':raise ValueError('Invalid dashboard home')
    return config


def probe(config):
    validate(config)
    interface=config['interface'];nic=Path('/sys/class/net')/interface
    if not (nic/'device').exists():raise RuntimeError('A physical wake-capable Ethernet adapter is required')
    if (nic/'wireless').exists():raise RuntimeError('Wi-Fi requires separately verified WoWLAN support; Ethernet WOL is not sufficient')
    if 'mem' not in Path('/sys/power/state').read_text().split():raise RuntimeError('Kernel does not expose suspend-to-RAM')
    text=run(['ethtool',interface])
    supported=re.search(r'^\s*Supports Wake-on:\s*(\S+)',text,re.M)
    if not supported or 'g' not in supported.group(1):raise RuntimeError(interface+' does not report magic-packet wake support')
    if config['sleep_mode']=='deep' and 'deep' not in Path('/sys/power/mem_sleep').read_text().replace('[','').replace(']','').split():
        raise RuntimeError('Deep sleep is unavailable; use current mode or different hardware')
    suspend_command()
    return nic


def uptime():return float(Path('/proc/uptime').read_text().split()[0])


def lock_paths(config):
    if config['role'] in ('tiny','laptop'):return [Path('/run/lock/car-dashboard-update.lock')]
    state=Path(config['home'])/'.local/share/drive-console/courier'
    return [state/'prepare.lock',state/'courier.lock']


def acquire(stack,config):
    for path in lock_paths(config):
        if config['role']=='duet':
            # Never create/chown files in a user-writable tree as root. The
            # installed courier creates these locks; until then remain awake.
            try:fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
            except FileNotFoundError:raise BlockingIOError('Duet courier has not initialized its locks')
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o644)
        file=stack.enter_context(os.fdopen(fd,'rb'))
        fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)


def wake_pending(config):
    # A failed check only leaves an already-running device awake.
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.connect((config['pi_address'],8799))
            sock.send(b'CAR_WAKE_STATUS_V1')
            status=json.loads(sock.recv(256))
        if config.get('require_registration'):
            mac=(Path('/sys/class/net')/config['interface']/'address').read_text().strip().lower()
            if mac not in status.get('knownMacs',[]):return False
        seconds=status['uptime']
        return status.get('schema')==1 and status.get('lastWake')==150 and type(seconds) in (int,float) and 0<=seconds<140
    except (OSError,ValueError,KeyError,TypeError):return False


def request_sleep():
    # logind may acknowledge a request before the kernel actually sleeps. Keep
    # update locks until a real suspend/resume cycle is observed, not merely a
    # successful command exit. BOOTTIME includes sleep; MONOTONIC excludes it.
    offset=time.clock_gettime(time.CLOCK_BOOTTIME)-time.monotonic()
    subprocess.run(suspend_command(),check=True,timeout=45)
    deadline=time.monotonic()+45
    while time.clock_gettime(time.CLOCK_BOOTTIME)-time.monotonic()-offset<.05:
        if time.monotonic()>=deadline:raise RuntimeError('No suspend/resume observed; staying awake, check sleep inhibitors/logs')
        time.sleep(.25)


def sleep_once(config,test=False):
    validate(config)
    if not test and config.get('armed') is not True:
        print('Boot standby unarmed; staying awake',flush=True);return
    if not test and uptime()>=120:
        print('Missed early boot standby window; staying awake for Pi',flush=True);return
    if not test:
        while uptime()<15:time.sleep(1)
    nic=probe(config)
    while test or uptime()<120:
        try:
            with ExitStack() as stack:
                acquire(stack,config)
                if not test and uptime()>=120:break
                # A sleeping USB gadget or dead cable cannot receive a magic packet.
                if (nic/'carrier').read_text().strip()!='1':raise BlockingIOError('Wake Ethernet link is down')
                run(['ethtool','-s',config['interface'],'wol','g'])
                text=run(['ethtool',config['interface']])
                if not re.search(r'^\s*Wake-on:\s*g\s*$',text,re.M):raise RuntimeError('Driver did not retain magic-packet wake setting')
                wake=nic/'device/power/wakeup'
                if wake.exists():wake.write_text('enabled\n')
                if config['sleep_mode']=='deep':Path('/sys/power/mem_sleep').write_text('deep\n')
                if not test and (uptime()>=120 or not wake_pending(config)):
                    if config.get('require_registration'):raise BlockingIOError('Waiting for Pi to register this adapter before standby')
                    print('Pi wake window is unknown or nearly over; staying awake',flush=True);return
                print('Suspending once; Pi must wake this adapter. Waiting for resume.',flush=True)
                # The parent holds updater locks through suspend and releases on resume.
                request_sleep()
                print('Resumed; staying awake for the rest of this boot',flush=True)
                return
        except BlockingIOError:
            if test:raise RuntimeError('Update is busy or wake link is down; staying awake')
            time.sleep(3)
    print('Updates/link were not ready before 120 seconds; staying awake',flush=True)


def ac_connected(root=Path('/sys/class/power_supply')):
    values=[]
    for path in root.glob('*'):
        try:
            if (path/'type').read_text().strip() in ('Mains','USB','USB_C','USB_PD'):
                values.append((path/'online').read_text().strip()=='1')
        except OSError:continue
    return any(values) if values else None


def lid_closed(root=Path('/proc/acpi/button/lid')):
    values=[]
    for path in root.glob('*/state'):
        try:
            value=path.read_text().lower()
            if 'closed' in value:values.append(True)
            elif 'open' in value:values.append(False)
        except OSError:continue
    return all(values) if values else None


def pi_registered(config):
    """Only a reply from the configured Pi is accepted, on the car Ethernet NIC."""
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,config['interface'].encode()+b'\0')
            sock.settimeout(2);sock.connect((config['pi_address'],8799));sock.send(b'CAR_WAKE_STATUS_V1')
            reply=json.loads(sock.recv(1024))
        mac=(Path('/sys/class/net')/config['interface']/'address').read_text().strip().lower()
        return reply.get('schema')==1 and mac in reply.get('knownMacs',[])
    except (OSError,ValueError,TypeError):return False


class LaptopSession:
    """A missing network alone cannot suspend a portable laptop."""
    def __init__(self):
        self.armed=False;self.off_since=None
    def observe(self,now,pi_online,ac,lid,delay=90):
        if pi_online and ac is True:
            self.armed=True;self.off_since=None;return 'car-active'
        if not self.armed:return 'waiting-confirmed-car-session'
        if pi_online or ac is not False:
            self.off_since=None;return 'waiting-car-power-loss'
        if self.off_since is None:self.off_since=now
        if now-self.off_since<delay:return 'car-off-grace-period'
        if lid is False:
            self.armed=False;self.off_since=None;return 'portable-use'
        if lid is not True:return 'pending-lid-sensor'
        return 'ready-deep-sleep'
    def resumed(self):
        self.armed=False;self.off_since=None


def write_laptop_status(value,path=Path('/etc/car-dashboard/laptop-power-status.json')):
    # Root-owned status, atomically replaced without touching any user files.
    import tempfile
    value=dict(value,updated=time.time())
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
        json.dump(value,stream);stream.flush();os.fsync(stream.fileno());pending=Path(stream.name)
    pending.chmod(0o644);os.replace(pending,path)


def laptop_memory_mode(text):
    modes=text.replace('[','').replace(']','').split()
    if 'deep' in modes:return 'deep'
    if 's2idle' in modes:return 's2idle'
    raise RuntimeError('No supported memory sleep mode is exposed')


def laptop_sleep(config):
    """Hold both the Git role refresh and application update locks through resume."""
    nic=probe(dict(config,sleep_mode='current'))
    with ExitStack() as stack:
        acquire(stack,config)
        refresh=stack.enter_context(Path('/var/lib/car-dashboard-fleet/refresh.lock').open('a'))
        fcntl.flock(refresh,fcntl.LOCK_EX|fcntl.LOCK_NB)
        run(['ethtool','-s',config['interface'],'wol','g'])
        if not re.search(r'^\s*Wake-on:\s*g\s*$',run(['ethtool',config['interface']]),re.M):
            raise RuntimeError('Ethernet driver did not retain wake setting')
        wake=nic/'device/power/wakeup'
        if wake.exists():wake.write_text('enabled\n')
        if ac_connected() is not False or lid_closed() is not True or pi_registered(config):
            raise BlockingIOError('Car/lid state changed before sleep')
        modes=Path('/sys/power/mem_sleep');previous=re.search(r'\[([^]]+)\]',modes.read_text())
        modes.write_text(laptop_memory_mode(modes.read_text())+'\n')
        # Flush completed writes; applications retain normal sleep inhibitors.
        try:
            run(['sync','-f',config['home']]) # Local filesystem; avoid a dead shared mount.
            request_sleep()
        finally:
            if previous:modes.write_text(previous.group(1)+'\n')


def laptop_monitor(path):
    session=LaptopSession();inhibitor=None;registered_adapter=None
    try:
        while True:
            try:
                config=json.loads(Path(path).read_text())
                if config.get('role')!='laptop' or not config.get('enabled',True):return
                phase='disabled'
                if config.get('laptop_sleep',True):
                    nic=config.get('interface')
                    status=Path('/etc/car-dashboard/fleet-status.json')
                    if not nic and status.exists():nic=json.loads(status.read_text()).get('interface')
                    if not nic:
                        write_laptop_status({'phase':'pending-ethernet','model':hardware_model()});time.sleep(10);continue
                    candidate={'schema':1,'role':'laptop','home':config['home'],'interface':nic,
                               'pi_address':'172.16.42.3','sleep_mode':'current'}
                    validate(candidate)
                    capability='reported-supported';capability_detail=None;mode=None
                    try:
                        probe(candidate);mode=laptop_memory_mode(Path('/sys/power/mem_sleep').read_text())
                    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
                        capability='pending-hardware-support';capability_detail=str(exc)[:256]
                    online=pi_registered(candidate)
                    adapter=(nic,(Path('/sys/class/net')/nic/'address').read_text().strip().lower())
                    if registered_adapter is not None and registered_adapter!=adapter:session.resumed()
                    ac=ac_connected();lid=lid_closed()
                    if online and ac is True:registered_adapter=adapter
                    phase=session.observe(time.monotonic(),online,ac,lid,max(30,int(config.get('laptop_sleep_delay',90))))
                    if session.armed and inhibitor is None and shutil.which('systemd-inhibit'):
                        inhibitor=subprocess.Popen(['systemd-inhibit','--what=handle-lid-switch','--who=Car laptop',
                            '--why=Keep spare laptop ready during confirmed car session','--mode=block','/bin/cat'],
                            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    if phase=='ready-deep-sleep':
                        write_laptop_status({'phase':'requesting-deep-sleep'})
                        laptop_sleep(candidate);session.resumed();phase='resumed-waiting-car-session'
                        if pi_registered(candidate) and ac_connected() is True:
                            phase=session.observe(time.monotonic(),True,True,lid_closed())
                    write_laptop_status({'phase':phase,'ac':ac,'lidClosed':lid,'piRegistered':online,
                                         'model':hardware_model(),'mode':mode,'physicalWakeVerified':False,
                                         'sleepCapability':capability,'capabilityDetail':capability_detail})
                else:
                    session.resumed();write_laptop_status({'phase':'disabled'})
                if inhibitor and (not session.armed or inhibitor.poll() is not None):
                    if inhibitor.stdin:inhibitor.stdin.close()
                    if inhibitor.poll() is None:
                        try:inhibitor.wait(timeout=5)
                        except subprocess.TimeoutExpired:inhibitor.terminate();inhibitor.wait(timeout=5)
                    inhibitor=None
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
                write_laptop_status({'phase':'pending-capability-or-update-lock','detail':str(exc)[:300]})
            time.sleep(10)
    finally:
        if inhibitor:
            if inhibitor.stdin:inhibitor.stdin.close()
            if inhibitor.poll() is None:inhibitor.terminate()


def hardware_model():
    result={}
    for key,name in [('vendor','sys_vendor'),('model','product_name')]:
        try:result[key]=(Path('/sys/class/dmi/id')/name).read_text().strip()
        except OSError:pass
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if line.startswith('PRETTY_NAME='):result['os']=line.split('=',1)[1].strip().strip(chr(34))
    except OSError:pass
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check','boot','test-suspend','laptop'])
    parser.add_argument('--config',default='/etc/car-dashboard/boot-standby.json')
    args=parser.parse_args()
    if args.action=='laptop':
        if os.geteuid()!=0:raise SystemExit('Use the normal laptop installer')
        laptop_monitor(args.config);raise SystemExit(0)
    if args.action=='boot':
        while not Path(args.config).exists() and uptime()<120:time.sleep(1)
        if not Path(args.config).exists():raise SystemExit('Role configuration is not ready; staying awake')
    config=json.loads(Path(args.config).read_text())
    if args.action=='check':
        probe(config);print('Reported capabilities pass. Physical wake test still required.')
    else:
        if os.geteuid()!=0:raise SystemExit('Suspend preparation must run as root')
        sleep_once(config,test=args.action=='test-suspend')
