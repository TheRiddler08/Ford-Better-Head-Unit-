#!/usr/bin/env python3
"""Automatic per-role car LAN, Wi-Fi sharing, wake discovery and provisioning.

Runs only on explicitly installed car devices. Uses fixed role addresses, no DHCP
server, one elected Wi-Fi gateway and private firewall chains. Never runs a shell
from a peer announcement. LAN discovery is for the trusted physical car switch.
"""
import argparse
import ast
from contextlib import ExitStack
import fcntl
import hashlib
import grp
import ipaddress
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import select
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from urllib.request import Request,urlopen

ADDRESSES={'duet':'172.16.42.1','tiny':'172.16.42.2','pi':'172.16.42.3','laptop':'172.16.42.4'}
SUBNET='172.16.42.0/24'
BROADCAST='172.16.42.255'
PORT=8798
VERSION=1
UPDATE_CHECK_INTERVAL=240
BASE=Path('/usr/local/lib/car-dashboard')
CONFIG=Path('/etc/car-dashboard/fleet.json')
STATE=Path('/var/lib/car-dashboard-fleet')
STATUS=Path('/etc/car-dashboard/fleet-status.json')
ORIGIN='https://github.com/TheRiddler08/Ford-Better-Head-Unit-.git'
SOURCES={'routing_setup.py':'dev/setup_us_routing.py','virtual_obd.py':'recorder/virtual_obd.py','virtual_gps.py':'recorder/virtual_gps.py','fleet.py':'deploy/fleet.py','wake.py':'power/wake.py','standby.py':'power/standby.py'}
REQUIRED=('nmcli','ip','iptables','dnsmasq','curl','ethtool')
STOP=False


def run(args,timeout=25,check=True):
    result=subprocess.run([str(a) for a in args],capture_output=True,text=True,timeout=timeout)
    if check and result.returncode:raise RuntimeError(str(args[0])+' failed: '+result.stderr[-600:].strip())
    return result.stdout.strip() if check else result


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=value if isinstance(value,bytes) else (json.dumps(value,sort_keys=True)+'\n').encode()
    if path.exists() and path.read_bytes()==data:return
    if path.is_symlink():raise ValueError('Refusing symlink: '+str(path))
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as file:
        file.write(data);file.flush();os.fsync(file.fileno());pending=Path(file.name)
    os.chmod(pending,0o644);os.replace(pending,path)
    fd=os.open(path.parent,os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def load(path,default):return json.loads(Path(path).read_text()) if Path(path).exists() else default


def eligible_peer(message,address,now):
    if not isinstance(message,dict) or message.get('schema')!=1:return None
    role=message.get('role')
    if role not in ADDRESSES or address!=ADDRESSES[role]:return None
    mac=message.get('mac','')
    if not isinstance(mac,str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac):return None
    raw=bytes.fromhex(mac.replace(':',''))
    if raw==b'\0'*6 or raw[0]&1:return None
    return {'role':role,'mac':mac.lower(),'internet':message.get('internet') is True,'seen':now}


def elect(role,online,peers,now):
    candidates={role} if online and role in ('pi','duet','laptop') else set()
    for name,peer in peers.items():
        if name in ('pi','duet','laptop') and peer.get('internet') is True and 0<=now-peer.get('seen',0)<=15:candidates.add(name)
    return next((name for name in ('pi','duet','laptop') if name in candidates),None)


def devices():
    return [line.split(':',2)[:2] for line in run(['nmcli','-t','-f','DEVICE,TYPE','device','status']).splitlines() if ':' in line]


def choose_interface(config):
    candidates=[name for name,kind in devices() if kind=='ethernet' and (Path('/sys/class/net')/name/'device').exists()]
    explicit=config.get('interface')
    if explicit:return explicit if explicit in candidates else None
    if len(candidates)==1:return candidates[0]
    known=[]
    for nic in candidates:
        addresses=run(['ip','-j','-4','address','show','dev',nic])
        if any(a.get('local')==ADDRESSES[config['role']] for row in json.loads(addresses) for a in row.get('addr_info',[])):known.append(nic)
    if len(known)==1:return known[0]
    return None


def ensure_lan(config,nic):
    address=ADDRESSES[config['role']]
    info=json.loads(run(['ip','-j','-4','address','show','dev',nic]))
    current=[a['local'] for row in info for a in row.get('addr_info',[]) if a.get('scope')=='global']
    if address in current:return
    if any(ip in ADDRESSES.values() and ip!=address for ip in current):raise RuntimeError('Ethernet already has another car role address')
    if current:
        # Add the car identity without removing an existing DHCP lease/default route.
        run(['ip','address','add',address+'/24','dev',nic]);return
    name='car-fleet-'+config['role']
    matches=run(['nmcli','-t','-f','NAME,UUID','connection','show']).splitlines()
    uuids=[line.split(':')[1] for line in matches if line.startswith(name+':')]
    if len(uuids)>1:raise RuntimeError('Ambiguous managed Ethernet profile')
    if not uuids:
        run(['nmcli','connection','add','type','ethernet','con-name',name,'ifname',nic,
             'connection.autoconnect','yes','connection.autoconnect-priority','100',
             'ipv4.method','manual','ipv4.addresses',address+'/24','ipv4.never-default','yes',
             'ipv4.ignore-auto-dns','yes','ipv6.method','disabled','802-3-ethernet.cloned-mac-address','permanent'])
        uuids=[run(['nmcli','-g','connection.uuid','connection','show',name])]
    run(['nmcli','--wait','10','connection','up','uuid',uuids[0],'ifname',nic])


def wifi_uplink():
    for nic,kind in devices():
        if kind!='wifi':continue
        state=run(['nmcli','-g','GENERAL.STATE','device','show',nic])
        if not state.startswith('100'):continue
        # Bind the probe to Wi-Fi, so a peer's shared Internet cannot advertise
        # itself back as a new uplink and create a routing loop. Keep TLS/proxy trust.
        online=False
        # DNS can still point at a disconnected car gateway after a hotspot or
        # Ethernet change. The numeric fallback keeps TLS verification and the
        # configured proxy, but needs no destination-host DNS resolution.
        for url in ('https://github.com/','https://1.1.1.1/cdn-cgi/trace'):
            result=run(['curl','--ipv4','--interface',nic,'--head','--silent','--show-error','--output','/dev/null',
                        '--connect-timeout','3','--max-time','5',url],timeout=7,check=False)
            if result.returncode==0:online=True;break
        if online:
            text=run(['nmcli','-g','IP4.DNS','device','show',nic])
            dns=[]
            for line in text.splitlines():
                try:
                    ip=ipaddress.IPv4Address(line.strip())
                    if ip not in ipaddress.IPv4Network(SUBNET) and not ip.is_loopback:dns.append(str(ip))
                except ValueError:pass
            return nic,list(dict.fromkeys(dns+['1.1.1.1','9.9.9.9']))
    return None,[]


class Sharing:
    def __init__(self):self.gateway_state=None;self.route_state=None;self.route_checked=0;self.dns=None
    def firewall(self,lan,wan,control=True):
        # Scope all rules to our chains and car subnet. No flush ruleset, global
        # DROP policy changes, DHCP service, or edits to Docker/Tailscale chains.
        for table,chain in [('filter','CAR_FLEET_FWD'),('nat','CAR_FLEET_NAT'),('filter','CAR_FLEET_IN')]:
            run(['iptables','-w','5','-t',table,'-N',chain],check=False)
            run(['iptables','-w','5','-t',table,'-F',chain])
        if control:
            args=['iptables','-w','5','-t','filter']
            run([*args,'-A','CAR_FLEET_IN','-p','udp','-m','multiport','--dports','8796,8798,8799','-j','ACCEPT'])
            if run([*args,'-C','INPUT','-i',lan,'-s',SUBNET,'-j','CAR_FLEET_IN'],check=False).returncode:
                run([*args,'-I','INPUT','1','-i',lan,'-s',SUBNET,'-j','CAR_FLEET_IN'])
            if wan:
                for protocol in ('tcp','udp'):run([*args,'-A','CAR_FLEET_IN','-p',protocol,'--dport','53','-j','ACCEPT'])
        if not wan:return
        Path('/proc/sys/net/ipv4/ip_forward').write_text('1\n')
        rules=[('filter','CAR_FLEET_FWD',['-s',SUBNET,'-i',lan,'-o',wan,'-j','ACCEPT']),
               ('filter','CAR_FLEET_FWD',['-d',SUBNET,'-i',wan,'-o',lan,'-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT']),
               ('filter','CAR_FLEET_FWD',['-d',SUBNET,'-i',wan,'-o',lan,'-j','DROP']),
               ('nat','CAR_FLEET_NAT',['-s',SUBNET,'-o',wan,'-j','MASQUERADE'])]
        for table,chain,args in rules:run(['iptables','-w','5','-t',table,'-A',chain,*args])
        for table,parent,scope,chain in [('filter','FORWARD',['-s',SUBNET],'CAR_FLEET_FWD'),
                                        ('filter','FORWARD',['-d',SUBNET],'CAR_FLEET_FWD'),
                                        ('nat','POSTROUTING',['-s',SUBNET],'CAR_FLEET_NAT')]:
            args=['iptables','-w','5','-t',table]
            if run([*args,'-C',parent,*scope,'-j',chain],check=False).returncode:
                run([*args,'-I',parent,'1',*scope,'-j',chain])
    def route_ready(self,nic,gateway):
        try:
            routes=json.loads(run(['ip','-j','-4','route','show','default','dev',nic,'proto','242']))
            if not any(row.get('gateway')==gateway and row.get('metric')==50 for row in routes):return False
            if shutil.which('resolvectl'):
                dns=run(['resolvectl','dns',nic]).split(':',1)[-1].split()
                domains=run(['resolvectl','domain',nic]).split(':',1)[-1].split()
                default=run(['resolvectl','default-route',nic]).split(':',1)[-1].strip().lower()
                return gateway in dns and '~.' in domains and default=='yes'
            return gateway in run(['nmcli','-g','IP4.DNS','device','show',nic]).splitlines()
        except (ValueError,TypeError,RuntimeError,subprocess.SubprocessError):return False

    def route(self,nic,gateway):
        desired=(nic,gateway)
        same=desired==self.route_state
        if same:
            if gateway is None:return
            if time.monotonic()-self.route_checked<10:return
            if self.route_ready(nic,gateway):self.route_checked=time.monotonic();return
        previous=self.route_state[0] if self.route_state else nic
        if not same:
            removed=run(['ip','-4','route','del','default','dev',previous,'proto','242','metric','50'],check=False)
            if removed.returncode==0 or (self.route_state and self.route_state[1]):
                if shutil.which('resolvectl'):run(['resolvectl','revert',previous],check=False)
                else:run(['nmcli','device','modify',previous,'ipv4.dns','','ipv4.dns-priority','0'],check=False)
        if gateway:
            run(['ip','-4','route','replace','default','via',gateway,'dev',nic,'proto','242','metric','50'])
            if shutil.which('resolvectl'):
                run(['resolvectl','dns',nic,gateway]);run(['resolvectl','domain',nic,'~.']);run(['resolvectl','default-route',nic,'yes'])
            else:run(['nmcli','device','modify',nic,'ipv4.dns',gateway,'ipv4.ignore-auto-dns','yes','ipv4.dns-priority','-50'])
        self.route_state=desired;self.route_checked=time.monotonic()
    def apply(self,nic,wan,servers,role,winner,online):
        desired=(nic,wan,tuple(servers)) if winner==role and wan else (nic,None,())
        if desired!=self.gateway_state:
            if self.dns:
                self.dns.terminate()
                try:self.dns.wait(timeout=5)
                except subprocess.TimeoutExpired:self.dns.kill();self.dns.wait()
                self.dns=None
            self.firewall(nic,desired[1])
            if desired[1]:
                args=['dnsmasq','--keep-in-foreground','--conf-file=/dev/null','--bind-interfaces','--no-resolv','--no-hosts',
                      '--listen-address='+ADDRESSES[role],'--no-dhcp-interface='+nic,'--cache-size=300']
                for server in servers:args+=['--server='+server+'@'+wan]
                self.dns=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.gateway_state=desired
        if self.dns and self.dns.poll() is not None:
            self.gateway_state=None;raise RuntimeError('Car DNS forwarder failed; check port 53 availability')
        self.route(nic,ADDRESSES[winner] if winner and winner!=role and not online else None)
    def close(self):
        if self.dns:
            self.dns.terminate()
            try:self.dns.wait(timeout=5)
            except subprocess.TimeoutExpired:self.dns.kill();self.dns.wait()
        if self.gateway_state:self.firewall(self.gateway_state[0],None,control=False)
        if self.route_state:self.route(self.route_state[0],None)


def write_wake(config,nic,peers):
    role=config['role']
    if role=='pi':
        rows=[{'name':name,'mac':peer['mac'],'broadcast':BROADCAST,'interface':nic}
              for name,peer in sorted(peers.items()) if name in ('tiny','duet','laptop')]
        save('/etc/car-dashboard/pi-wake.json',{'schema':1,'status':{'bind':ADDRESSES['pi'],'interface':nic},'targets':rows})
    elif role=='laptop':
        # Laptop sleeps only after a confirmed car-off transition, never at boot.
        return 'car-off-monitor'
    else:
        # Enable automatically only when the installed driver reports the needed
        # capability. Unsupported hardware is status, not an installer lockout.
        path=Path(__file__).resolve().parent
        candidate={'schema':1,'role':role,'interface':nic,'home':config['home'],'pi_address':ADDRESSES['pi'],
                   'sleep_mode':'current','armed':False,'require_registration':True}
        with tempfile.NamedTemporaryFile(mode='w') as proposed:
            json.dump(candidate,proposed);proposed.flush()
            result=run(['/usr/bin/python3','-I',path/'standby.py','check','--config',proposed.name],check=False)
        candidate['armed']=config.get('boot_standby',True) and result.returncode==0
        save('/etc/car-dashboard/boot-standby.json',candidate)
        return 'armed' if candidate['armed'] else 'unsupported-or-disabled'
    return 'pi-sender'


def daemon(config):
    global STOP
    signal.signal(signal.SIGTERM,lambda *_:globals().__setitem__('STOP',True))
    signal.signal(signal.SIGINT,lambda *_:globals().__setitem__('STOP',True))
    STATE.mkdir(parents=True,exist_ok=True)
    peers=load(STATE/'peers.json',{})
    # MACs persist for sleeping targets; stale Internet availability never does.
    for value in peers.values():value['seen']=0;value['internet']=False
    sharing=Sharing();last_probe=0;wan=None;servers=[];nic=None;last_wake=0;healthy=True
    try:
        while not STOP:
            missing=[name for name in REQUIRED if not shutil.which(name)]
            if missing:
                save(STATUS,{'phase':'waiting-dependencies','missing':missing,'role':config['role']});time.sleep(5);continue
            chosen=choose_interface(config)
            if not chosen:
                save(STATUS,{'phase':'waiting-ethernet','role':config['role'],'detail':'No unique physical Ethernet adapter; plug in switch or set interface in fleet.json'})
                time.sleep(3);continue
            try:
                ensure_lan(config,chosen)
                if nic!=chosen:
                    sharing.close();sharing=Sharing();nic=chosen
                mac=(Path('/sys/class/net')/nic/'address').read_text().strip().lower()
                now=time.monotonic()
                if now-last_probe>=15 or not last_probe:
                    wan,servers=wifi_uplink();last_probe=now
                # Receivers bind the physical Ethernet interface; source role/IP
                # checks reject Wi-Fi, Tailscale, unknown roles and oversized data.
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
                    receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    receiver.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,nic.encode()+b'\0')
                    receiver.bind(('',PORT))
                    sender.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
                    sender.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,nic.encode()+b'\0')
                    sender.bind((ADDRESSES[config['role']],0))
                    sender.sendto(json.dumps({'schema':1,'role':config['role'],'mac':mac,'internet':bool(wan) and healthy}).encode(),(BROADCAST,PORT))
                    until=time.monotonic()+3
                    while not STOP and time.monotonic()<until:
                        ready,_,_=select.select([receiver],[],[],.3)
                        if not ready:continue
                        data,peer=receiver.recvfrom(1024)
                        try:valid=eligible_peer(json.loads(data),peer[0],time.monotonic())
                        except (ValueError,TypeError):continue
                        if valid and valid['role']!=config['role']:peers[valid['role']]=valid
                checkpoint=peers
                if config['role']=='pi' and config.get('pi_storage_client',True):
                    checkpoint={role:{'role':role,'mac':peer['mac'],'seen':0,'internet':False} for role,peer in peers.items()}
                save(STATE/'peers.json',checkpoint)
                winner=elect(config['role'],bool(wan),peers,time.monotonic())
                sharing.apply(nic,wan,servers,config['role'],winner,bool(wan));healthy=True
                mode=None
                if time.monotonic()-last_wake>15 or not last_wake:
                    mode=write_wake(config,nic,peers);last_wake=time.monotonic()
                previous=load(STATUS,{})
                save(STATUS,{'phase':'ready','role':config['role'],'interface':nic,'address':ADDRESSES[config['role']],
                             'mac':mac,'gateway':winner,'wifiInternet':bool(wan),'standby':mode or previous.get('standby'),
                             'peers':{key:value['mac'] for key,value in peers.items()},'updated':int(time.time())})
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
                healthy=False
                save(STATUS,{'phase':'retrying','role':config['role'],'error':str(exc)[-500:]});time.sleep(3)
    finally:sharing.close()


def publish(sources,base=BASE):
    for name in SOURCES:
        if name not in sources:raise ValueError('Incomplete fleet installer')
        ast.parse(sources[name],filename=name)
    identity=hashlib.sha256(b''.join(sources[name] for name in sorted(SOURCES))).hexdigest()
    versions=Path(base)/'fleet-versions';versions.mkdir(parents=True,exist_ok=True)
    destination=versions/identity
    if destination.exists():
        if any((destination/name).read_bytes()!=sources[name] for name in SOURCES):raise ValueError('Installed fleet version was edited')
    else:
        with tempfile.TemporaryDirectory(dir=versions) as temp:
            temp=Path(temp)
            for name in SOURCES:save(temp/name,sources[name])
            os.chmod(temp,0o755);os.replace(temp,destination)
    link=Path(base)/'fleet-next'
    if link.is_symlink():link.unlink()
    if link.exists():raise ValueError('Unmanaged fleet selector')
    link.symlink_to(Path('fleet-versions')/identity);os.replace(link,Path(base)/'fleet-current')
    return destination


def native_dependencies(offline=False,role=None):
    needed=list(REQUIRED)
    if role=='tiny':needed+=['gpsd','osmium']
    pi_client=role=='pi' and load(CONFIG,{}).get('pi_storage_client',True)
    if role in ADDRESSES and shutil.which('apt-get'):
        needed+=['mfsmount','mfssetgoal'] if pi_client else ['mfsmount','mfschunkserver','ffprobe']+(['mfsmaster'] if role=='tiny' else ['mfsmetalogger'])
    if role in ('duet','tiny','laptop') and Path('/run/openrc').exists():needed.append('loginctl')
    libraries=('cryptography','paho.mqtt.client','serial','nacl')
    missing=[name for name in needed if not shutil.which(name)]
    for library in libraries:
        try:available=importlib.util.find_spec(library) is not None
        except ModuleNotFoundError:available=False
        if not available:missing.append('python:'+library)
    if not missing or offline:return missing
    if shutil.which('apt-get'):
        run(['apt-get','update'],timeout=300)
        env=dict(os.environ,DEBIAN_FRONTEND='noninteractive')
        packages=['network-manager','iproute2','iptables','dnsmasq-base','curl','ethtool','python3-cryptography','python3-paho-mqtt','python3-serial','python3-nacl','moosefs-client']
        if role=='tiny':packages+=['gpsd','gpsd-clients','osmium-tool']
        if not pi_client:packages+=['moosefs-master','moosefs-chunkserver','moosefs-metalogger','ffmpeg']
        subprocess.run(['apt-get','install','-y','--no-install-recommends',*packages],env=env,check=True,timeout=600)
    elif shutil.which('apk'):
        run(['apk','add','networkmanager','iproute2','iptables','dnsmasq','curl','ethtool','elogind','py3-cryptography','py3-paho-mqtt','py3-pyserial','py3-pynacl'],timeout=600)
    remaining=[name for name in needed if not shutil.which(name)]
    for library in libraries:
        try:available=importlib.util.find_spec(library) is not None
        except ModuleNotFoundError:available=False
        if not available:remaining.append('python:'+library)
    return remaining


def unit(name,content):save(Path('/etc/systemd/system')/name,content.encode())


def install_pi_storage_client(config):
    """A small-card Pi mounts the pool but contributes no chunks or backups."""
    for service in ('car-storage-chunks','car-storage-metadata','car-storage-master'):
        if Path('/run/systemd/system').exists():run(['systemctl','disable','--now',service+'.service'],check=False)
        elif Path('/run/openrc').exists():
            run(['rc-update','del',service,'default'],check=False);run(['rc-service',service,'stop'],check=False)
    executable=shutil.which('mfsmount')
    if not executable:
        save(STATE/'storage.json',{'phase':'pending-packages','mode':'client-only','missing':['mfsmount'],'contributionBytes':0})
        return ['storage:mfsmount']
    mount=Path('/car-data');mount.mkdir(mode=0o755,exist_ok=True)
    if not mount.is_mount() and any(mount.iterdir()):
        save(STATE/'storage.json',{'phase':'pending-nonempty-mountpoint','mode':'client-only','contributionBytes':0})
        return ['storage:nonempty-mountpoint']
    command=[executable,'-f','-H',ADDRESSES['tiny'],'-o','allow_other,nosuid,nodev,noexec',str(mount)]
    if Path('/run/systemd/system').exists():
        unit('car-storage-mount.service','[Unit]\nDescription=Pi shared-storage client (no SD contribution)\nAfter=car-fleet.service\n[Service]\nExecStart='+shlex.join(command)+'\nRestart=always\nRestartSec=15\nTimeoutStopSec=30\n[Install]\nWantedBy=multi-user.target\n')
        run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','car-storage-mount.service'])
        run(['systemctl','try-restart','car-storage-mount.service'],check=False)
    elif Path('/run/openrc').exists():
        path=Path('/etc/init.d/car-storage-mount')
        save(path,('#!/sbin/openrc-run\nsupervisor="supervise-daemon"\ncommand='+shlex.quote(executable)+'\ncommand_args='+shlex.quote(shlex.join(command[1:]))+'\nrespawn_delay=15\ndepend() { need car-fleet; }\n').encode())
        path.chmod(0o755);run(['rc-update','add','car-storage-mount','default']);run(['rc-service','car-storage-mount','restart'],check=False)
    save(STATE/'storage.json',{'phase':'configured-awaiting-cluster','mode':'client-only',
        'master':ADDRESSES['tiny'],'mount':'/car-data','contributionBytes':0,'bulkOffline':'defer-no-sd-fallback'})
    return []


def install_pi_storage_policy(config):
    if config['role']!='pi' or not config.get('pi_storage_client',True):return
    if Path('/run/systemd/system').exists():
        path=Path('/etc/systemd/journald.conf.d/60-car-pi-storage.conf')
        content=b'[Journal]\nStorage=volatile\nRuntimeMaxUse=32M\nRuntimeMaxFileSize=4M\n'
        changed=not path.exists() or path.read_bytes()!=content
        save(path,content)
        if changed:run(['systemctl','restart','systemd-journald.service'],check=False)
    if shutil.which('apt-get'):
        save('/etc/apt/apt.conf.d/80-car-pi-storage',b'APT::Keep-Downloaded-Packages "false";\nBinary::apt-get::APT::Keep-Downloaded-Packages "false";\n')
        run(['apt-get','clean'],check=False)


def install_storage(config):
    """Configure packaged MooseFS, without formatting disks or relocating live DBs."""
    role=config['role'];base=Path('/var/lib/car-storage');cfg=Path('/etc/car-dashboard/storage')
    if not config.get('shared_storage',True):
        for service in ('car-storage-master','car-storage-chunks','car-storage-metadata','car-storage-mount'):
            if Path('/run/systemd/system').exists():run(['systemctl','disable','--now',service+'.service'],check=False)
            elif Path('/run/openrc').exists():
                run(['rc-update','del',service,'default'],check=False);run(['rc-service',service,'stop'],check=False)
        save(STATE/'storage.json',{'phase':'disabled'});return []
    if role=='pi' and config.get('pi_storage_client',True):return install_pi_storage_client(config)
    needed=['mfsmount','mfschunkserver']+(['mfsmaster'] if role=='tiny' else ['mfsmetalogger'])
    missing=[name for name in needed if not shutil.which(name)]
    if missing:
        save(STATE/'storage.json',{'phase':'pending-packages','missing':missing});return ['storage:'+name for name in missing]
    name=next((name for name in ('mfs','moosefs') if _has_user(name)),None)
    if name is None:
        save(STATE/'storage.json',{'phase':'pending-service-account'});return ['storage:service-account']
    account=pwd.getpwnam(name)
    base.mkdir(parents=True,exist_ok=True);cfg.mkdir(parents=True,exist_ok=True)
    def owned(path):
        path.mkdir(parents=True,exist_ok=True);os.chown(path,account.pw_uid,account.pw_gid);path.chmod(0o750)
    for part in ('chunks','chunk-state','master','metadata-backup'):owned(base/part)
    # Allocate existing free space, preserving an 8 GiB OS/recording reserve.
    capacity=load(cfg/'capacity.json',{})
    if not capacity:
        free=shutil.disk_usage(base).free
        limit=min(int(free*.6),free-8*1024**3)
        if limit<1024**3:
            save(STATE/'storage.json',{'phase':'pending-disk-space','reserveBytes':8*1024**3});return ['storage:disk-space']
        capacity={'bytes':limit};save(cfg/'capacity.json',capacity)
    common=f'WORKING_USER = {account.pw_name}\nWORKING_GROUP = {grp.getgrgid(account.pw_gid).gr_name}\nNICE_LEVEL = 0\nDISABLE_OOM_KILLER = 0\n'
    save(cfg/'hdd.cfg',f'{base}/chunks {capacity["bytes"]}\n'.encode())
    save(cfg/'chunk.cfg',(common+f'DATA_PATH = {base}/chunk-state\nMASTER_HOST = {ADDRESSES["tiny"]}\nBIND_HOST = {ADDRESSES[role]}\nCSSERV_LISTEN_HOST = {ADDRESSES[role]}\nHDD_CONF_FILENAME = {cfg}/hdd.cfg\nHDD_LEAVE_SPACE_DEFAULT = 8GiB\nHDD_FSYNC_BEFORE_CLOSE = 1\n').encode())
    services=[('car-storage-chunks',[shutil.which('mfschunkserver'),'-f','-c',str(cfg/'chunk.cfg'),'start'])]
    if role=='tiny':
        master=base/'master'
        if not (master/'metadata.mfs').exists() and not (master/'metadata.mfs.back').exists() and not (cfg/'master-initialized').exists():
            if any(master.iterdir()):
                save(STATE/'storage.json',{'phase':'pending-master-recovery'});return ['storage:master-recovery']
            candidates=[Path('/var/lib/mfs/metadata.mfs.empty'),Path('/var/lib/moosefs/metadata.mfs.empty')]
            empty=next((p for p in candidates if p.exists()),None)
            if empty is None:
                save(STATE/'storage.json',{'phase':'pending-master-seed'});return ['storage:master-seed']
            shutil.copy2(empty,master/'metadata.mfs');os.chown(master/'metadata.mfs',account.pw_uid,account.pw_gid)
            save(cfg/'master-initialized',b'initialized\n')
        exports=''.join(f'{ip} / rw,alldirs,mapall=65534:65534\n' for ip in ADDRESSES.values())
        save(cfg/'exports.cfg',exports.encode())
        save(cfg/'master.cfg',(common+f'DATA_PATH = {master}\nEXPORTS_FILENAME = {cfg}/exports.cfg\nMATOML_LISTEN_HOST = {ADDRESSES[role]}\nMATOCS_LISTEN_HOST = {ADDRESSES[role]}\nMATOCL_LISTEN_HOST = {ADDRESSES[role]}\n').encode())
        services.insert(0,('car-storage-master',[shutil.which('mfsmaster'),'-f','-a','-c',str(cfg/'master.cfg'),'start']))
    else:
        save(cfg/'metalogger.cfg',(common+f'DATA_PATH = {base}/metadata-backup\nMASTER_HOST = {ADDRESSES["tiny"]}\nBIND_HOST = {ADDRESSES[role]}\n').encode())
        services.append(('car-storage-metadata',[shutil.which('mfsmetalogger'),'-f','-c',str(cfg/'metalogger.cfg'),'start']))
    mount=Path('/car-data')
    if not mount.exists():mount.mkdir(mode=0o755)
    if not mount.is_mount() and any(mount.iterdir()):
        save(STATE/'storage.json',{'phase':'pending-nonempty-mountpoint'});return ['storage:nonempty-mountpoint']
    services.append(('car-storage-mount',[shutil.which('mfsmount'),'-f','-H',ADDRESSES['tiny'],'-o','allow_other,nosuid,nodev,noexec',str(mount)]))
    for name,command in services:
        worker=name=='car-data'
        if Path('/run/systemd/system').exists():
            extra=f'User={config["user"]}\nEnvironment=HOME={config["home"]}\nWorkingDirectory={config["repo"]}\n' if worker else ''
            unit(name+'.service','[Unit]\nDescription=Local car storage ('+name+')\nAfter=car-fleet.service\n[Service]\n'+extra+'ExecStart='+shlex.join(command)+'\nRestart=always\nRestartSec=15\nTimeoutStopSec=30\nUMask=0077\n[Install]\nWantedBy=multi-user.target\n')
            run(['systemctl','daemon-reload']);run(['systemctl','enable','--now',name+'.service']);run(['systemctl','try-restart',name+'.service'],check=False)
        elif Path('/run/openrc').exists():
            script=f'#!/sbin/openrc-run\nsupervisor="supervise-daemon"\ncommand={shlex.quote(command[0])}\ncommand_args={shlex.quote(shlex.join(command[1:]))}\nrespawn_delay=15\nrespawn_max=0\n'
            if worker:script+=f'command_user={shlex.quote(config["user"])}\ndirectory={shlex.quote(config["repo"])}\nexport HOME={shlex.quote(config["home"])}\n'
            script+='depend() { need car-fleet; }\n'
            save(Path('/etc/init.d')/name,script.encode());os.chmod(Path('/etc/init.d')/name,0o755)
            run(['rc-update','add',name,'default']);run(['rc-service',name,'restart'],check=False)
    save(STATE/'storage.json',{'phase':'configured-awaiting-cluster','master':ADDRESSES['tiny'],'mount':'/car-data','contributionBytes':capacity['bytes'],'replicaGoal':2})
    return []


def _has_user(name):
    try:pwd.getpwnam(name);return True
    except KeyError:return False


def install_laptop_power(config):
    current=BASE/'fleet-current'
    enabled=config.get('laptop_sleep',True)
    if Path('/run/systemd/system').exists():
        unit('car-laptop-power.service',f'''[Unit]
Description=Spare laptop car-off deep sleep
After=car-fleet.service systemd-logind.service
[Service]
ExecStart=/usr/bin/python3 -I {current}/standby.py laptop --config /etc/car-dashboard/fleet.json
Restart=on-failure
RestartSec=10
TimeoutStopSec=10
[Install]
WantedBy=multi-user.target
''')
        run(['systemctl','daemon-reload'])
        run(['systemctl','enable' if enabled else 'disable','--now','car-laptop-power.service'])
        if enabled:run(['systemctl','try-restart','car-laptop-power.service'],check=False)
    elif Path('/run/openrc').exists():
        save('/etc/init.d/car-laptop-power',f'''#!/sbin/openrc-run
supervisor="supervise-daemon"
command="/usr/bin/python3"
command_args="-I {current}/standby.py laptop --config /etc/car-dashboard/fleet.json"
respawn_delay=10
respawn_max=0
depend() {{ need car-fleet; }}
'''.encode())
        os.chmod('/etc/init.d/car-laptop-power',0o755)
        run(['rc-update','add' if enabled else 'del','car-laptop-power','default'])
        run(['rc-service','car-laptop-power','restart' if enabled else 'stop'],check=False)


def install_device_tools(config):
    """Independent input tools and navigation are available even with GPS disabled."""
    commit=config.get('commit') or git(config,'rev-parse','HEAD')
    for name,source in (('virtual_gps.py','recorder/virtual_gps.py'),('virtual_obd.py','recorder/virtual_obd.py'),('routing_setup.py','dev/setup_us_routing.py')):
        content=source_bytes(config,commit,source);ast.parse(content);save(BASE/name,content)
    data=Path('/var/lib/car-dashboard/routing')
    status={'region':'us','phase':'pending-data','message':'Full USA routing pack is not installed. Prepare it on a sufficiently sized host, then import the prepared pack.'}
    if (data/'prepared.json').is_file():
        metadata=load(data/'prepared.json',{})
        if not isinstance(metadata,dict):
            save('/etc/car-dashboard/navigation-status.json',{'region':'us','phase':'invalid-pack','message':'Prepared pack metadata must be a JSON object'});return
        status={'region':metadata.get('regions',['us']),'phase':'prepared','message':'Prepared routing data found; use routing_setup.py --serve-only to activate it.'}
        # Activate an already prepared, cached runtime without downloading data during updates.
        if shutil.which('docker') and isinstance(metadata.get('regions'),list) and 1<=len(metadata['regions'])<=51 and all(isinstance(value,str) and re.fullmatch('[a-z][a-z-]{1,31}',value) for value in metadata['regions']):
            result=run(['/usr/bin/python3',BASE/'routing_setup.py','--serve-only','--offline','--directory',data,'--region',*metadata['regions']],timeout=120,check=False)
            if result.returncode==0:status.update(phase='ready',message='Local routing engine started with prepared data')
            else:status.update(phase='pending-runtime',message='Prepared pack found; routing image must be carried or installed before offline activation')
    save('/etc/car-dashboard/navigation-status.json',status)


def install_gps(config):
    """Adopt Tiny's GPS client automatically; never probe arbitrary USB ports."""
    if not config.get('gps_recording',True):return
    service='car-gps-recorder.service'
    existing=Path('/etc/systemd/system')/service
    # Respect a deliberately stopped or masked existing recorder.
    if existing.is_symlink() and existing.resolve()==Path('/dev/null'):return
    # Existing units can carry a custom database path or deliberate opt-out.
    # Their checkout/overlay code is updated through the existing recorder path.
    if existing.exists():return
    commit=config.get('commit') or git(config,'rev-parse','HEAD')
    source=source_bytes(config,commit,'recorder/gps_recorder.py')
    ast.parse(source)
    target=Path(config['repo'])/'recorder/gps_recorder.py'
    unit(service,'''[Unit]
Description=Persistent local GPS movement history
After=network.target gpsd.service
[Service]
User='''+config['user']+'''
Environment=HOME='''+config['home']+'''
EnvironmentFile=-/etc/car-dashboard/dashboard.env
ExecStart=/usr/bin/python3 -u '''+str(target)+'''
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
UMask=0077
[Install]
WantedBy=multi-user.target
''')
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable',service])
    run(['systemctl','restart',service])


def install_app_updates(config):
    # Use committed source, not an editable root-executed checkout script.
    commit=config.get('commit') or git(config,'rev-parse','HEAD')
    source=source_bytes(config,commit,'deploy/update.sh')
    result=subprocess.run(['bash','-n'],input=source,capture_output=True)
    if result.returncode:raise ValueError('Committed dashboard updater has invalid syntax')
    header='#!/usr/bin/env bash\nREPO='+shlex.quote(config['repo'])+'\nAPP_USER='+shlex.quote(config['user'])+'\nBRANCH=main\nsystemctl is-active --quiet car-dashboard.service || exit 0\n'
    save('/usr/local/sbin/car-dashboard-update',header.encode()+source)
    os.chmod('/usr/local/sbin/car-dashboard-update',0o755)
    unit('car-dashboard-update.service','''[Unit]
Description=Automatic car dashboard update over available Internet
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/car-dashboard-update
TimeoutStartSec=300
''')
    unit('car-dashboard-update.timer','''[Unit]
Description=Automatically update Tiny when any Internet path is available
[Timer]
OnBootSec=180
OnCalendar=*-*-* *:0/4:00
RandomizedDelaySec=15
Persistent=true
[Install]
WantedBy=timers.target
''')
    run(['systemctl','daemon-reload'])
    disabled=Path('/etc/car-dashboard/updates-disabled').exists() or not config.get('app_updates',True)
    run(['systemctl','disable' if disabled else 'enable','--now','car-dashboard-update.timer'])


def settings_fingerprint(config):
    link=load(Path('/etc/car-dashboard/update-link.json'),{})
    value=config
    if link.get('home_ssh_target'):
        value=dict(config,_managed_home_tunnel={name:link.get(name) for name in ('home_ssh_target','home_tunnel_enabled')})
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def home_tunnel_unit(config,options):
    host=options.get('home_ssh_target')
    if not isinstance(host,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*',host):
        raise ValueError('Invalid home SSH target')
    user=config['user'];home=config['home']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',user) or not re.fullmatch(r'/[A-Za-z0-9_./-]+',home) or '..' in home.split('/'):
        raise ValueError('Unsupported user/home for managed tunnel')
    return f'''[Unit]
Description=Restricted authenticated home application tunnel
Wants=network-online.target
After=network-online.target
[Service]
User={user}
Environment=HOME={home}
ExecStart=/usr/bin/ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o IdentitiesOnly=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -i {home}/.ssh/car-home-tunnel -L 127.0.0.1:8791:127.0.0.1:8790 {host}
Restart=always
RestartSec=10
NoNewPrivileges=true
UMask=0077
[Install]
WantedBy=multi-user.target
'''


def systemd_units(role):
    current=BASE/'fleet-current'
    unit('car-fleet.service',f'''[Unit]
Description=Automatic car Ethernet and Wi-Fi sharing ({role})
Wants=NetworkManager.service
After=NetworkManager.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 -I {current}/fleet.py run
Restart=always
RestartSec=3
KillMode=mixed
TimeoutStopSec=20
[Install]
WantedBy=multi-user.target
''')
    unit('car-fleet-refresh.service',f'''[Unit]
Description=Apply role settings from verified car software updates
After=car-fleet.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I {current}/fleet.py refresh
TimeoutStartSec=900
''')
    config=load(CONFIG,{})
    link=load(Path('/etc/car-dashboard/update-link.json'),{})
    managed_tunnel=role=='tiny' and bool(link.get('home_ssh_target'))
    if managed_tunnel:unit('car-home-tunnel.service',home_tunnel_unit(config,link))
    user=config.get('user','nobody');repo=config.get('repo','/nonexistent');home=config.get('home','/nonexistent')
    for name,module in [('car-update-link','control.update_agent'),('car-radio-adapter','control.radio_gateway'),('car-messages','control.messages run'),('car-telemetry','control.telemetry'),('car-file-sync','control.file_sync'),('car-data','control.car_data')]:
        limits='Environment=CAR_STATE_DB_LIMIT_BYTES=16777216\n' if role=='pi' and config.get('pi_storage_client',True) else ''
        unit(name+'.service',f'''[Unit]
Description=Automatic resumable software link ({module})
After=car-fleet.service
[Service]
User={user}
SupplementaryGroups=dialout
Environment=HOME={home}
{limits}WorkingDirectory={repo}
ExecStart=/usr/bin/python3 -m {module}
Restart=always
RestartSec=10
UMask=0077
[Install]
WantedBy=multi-user.target
''')
    unit('car-fleet-refresh.timer','''[Unit]
Description=Reconcile installed car settings automatically
[Timer]
OnBootSec=20
OnUnitInactiveSec=30
[Install]
WantedBy=timers.target
''')
    if role=='pi':
        unit('car-wake@.service',f'''[Unit]
Description=Automatic car wake (%i)
After=car-fleet.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I {current}/wake.py
TimeoutStartSec=30
''')
        unit('car-wake-status.service',f'''[Unit]
Description=Pi wake-window registration on the car Ethernet switch
After=car-fleet.service
[Service]
ExecStart=/usr/bin/python3 -I {current}/wake.py --serve
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
''')
        for name,seconds in [('first',30),('retry',150)]:
            unit('car-wake-'+name+'.timer',f'''[Unit]
Description=Wake both car devices at boot +{seconds}s
[Timer]
OnBootSec={seconds}
AccuracySec=1
Unit=car-wake@{name}.service
[Install]
WantedBy=timers.target
''')
    elif role!='laptop':
        unit('car-boot-standby.service',f'''[Unit]
Description=Automatic supported standby waiting for Pi
After=car-fleet.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I {current}/standby.py boot
TimeoutStartSec=infinity
RemainAfterExit=yes
''')
        unit('car-boot-standby.timer','''[Unit]
Description=Consider supported car standby once per boot
[Timer]
OnBootSec=15
AccuracySec=1
[Install]
WantedBy=timers.target
''')
    if role=='laptop':install_laptop_power(config)
    run(['systemctl','daemon-reload'])
    if managed_tunnel:
        enabled=link.get('home_tunnel_enabled',True)
        run(['systemctl','enable' if enabled else 'disable','--now','car-home-tunnel.service'])
        if enabled:run(['systemctl','try-restart','car-home-tunnel.service'],check=False)
    run(['systemctl','disable','--now','car-duet-ethernet-'+role+'.timer'],check=False)
    run(['systemctl','enable','--now','car-fleet.service','car-fleet-refresh.timer','car-update-link.service','car-radio-adapter.service','car-messages.service','car-telemetry.service','car-file-sync.service','car-data.service'])
    run(['systemctl','restart','car-update-link.service','car-radio-adapter.service','car-messages.service','car-telemetry.service','car-file-sync.service','car-data.service'])
    # Never force suspend the SSH session that installed an update. Arming is
    # automatic, but the once-per-boot timer starts on the next device boot.
    extras=['car-wake-first.timer','car-wake-retry.timer','car-wake-status.service'] if role=='pi' else [] if role=='laptop' else ['car-boot-standby.timer']
    if extras:run(['systemctl','enable',*extras])
    run(['systemctl','restart','car-fleet.service'])
    if role=='pi':run(['systemctl','restart','car-wake-status.service'])


def openrc_units(role):
    current=BASE/'fleet-current'
    nm_service='NetworkManager' if Path('/etc/init.d/NetworkManager').exists() else 'networkmanager'
    run(['rc-update','add',nm_service,'default'])
    run(['rc-service',nm_service,'start'])
    for name,action in [('car-fleet','run'),('car-fleet-refresh','refresh-loop')]:
        save(Path('/etc/init.d')/name,f'''#!/sbin/openrc-run
name="Automatic car role provisioning"
supervisor="supervise-daemon"
command="/usr/bin/python3"
command_args="-I {current}/fleet.py {action}"
respawn_delay=3
respawn_max=0
output_log="/var/log/{name}.log"
error_log="/var/log/{name}.log"
depend() {{ need {nm_service}; after bootmisc; }}
'''.encode())
        os.chmod(Path('/etc/init.d')/name,0o755)
        run(['rc-update','add',name,'default'])
    if role!='pi' and shutil.which('loginctl'):
        run(['rc-update','add','elogind','boot'],check=False);run(['rc-service','elogind','start'],check=False)
    if role=='pi':
        save('/etc/init.d/car-wake-status',f'''#!/sbin/openrc-run
name="Pi wake timing"
supervisor="supervise-daemon"
command="/usr/bin/python3"
command_args="-I {current}/wake.py --serve"
respawn_delay=2
respawn_max=0
depend() {{ need car-fleet; }}
'''.encode())
        os.chmod('/etc/init.d/car-wake-status',0o755)
        run(['rc-update','add','car-wake-status','default'])
        run(['rc-service','car-wake-status','restart'])
    cfg=load(CONFIG,{})
    if role=='laptop':install_laptop_power(cfg)
    for name,module in [('car-update-link','control.update_agent'),('car-radio-adapter','control.radio_gateway'),('car-messages','control.messages run'),('car-telemetry','control.telemetry'),('car-file-sync','control.file_sync'),('car-data','control.car_data')]:
        limits='export CAR_STATE_DB_LIMIT_BYTES=16777216\n' if role=='pi' and cfg.get('pi_storage_client',True) else ''
        save(Path('/etc/init.d')/name,f'''#!/sbin/openrc-run
name="Automatic resumable update transport"
supervisor="supervise-daemon"
command="/usr/bin/python3"
command_args="-m {module}"
command_user="{cfg.get('user','nobody')}"
directory="{cfg.get('repo','/nonexistent')}"
export HOME="{cfg.get('home','/nonexistent')}"
{limits}respawn_delay=10
respawn_max=0
output_log="/var/log/{name}.log"
error_log="/var/log/{name}.log"
depend() {{ need car-fleet; }}
'''.encode())
        os.chmod(Path('/etc/init.d')/name,0o755)
        run(['rc-update','add',name,'default']);run(['rc-service',name,'restart'])
    # A Pi OpenRC boot worker handles the same absolute deadlines. Targets use
    # the same once-per-boot code, with elogind providing normal suspend policy.
    save('/etc/init.d/car-fleet-boot',f'''#!/sbin/openrc-run
name="Car wake/standby boot sequence"
command="/usr/bin/python3"
command_args="-I {current}/fleet.py boot"
command_background=true
pidfile="/run/car-fleet-boot.pid"
output_log="/var/log/car-fleet-boot.log"
error_log="/var/log/car-fleet-boot.log"
depend() {{ need car-fleet; }}
'''.encode())
    os.chmod('/etc/init.d/car-fleet-boot',0o755)
    run(['rc-update','add','car-fleet-boot','default'])
    run(['rc-service','car-fleet','restart']);run(['rc-service','car-fleet-refresh','start'])


def install(role,repo,user,sources,offline=False,commit=None,retry_missing=False):
    if os.geteuid()!=0:raise ValueError('Use the normal device installer, which requests sudo once')
    if role not in ADDRESSES:raise ValueError('Unknown role')
    account=pwd.getpwnam(user)
    if account.pw_uid==0:raise ValueError('The dashboard account must not be root')
    repo=Path(repo).resolve()
    if repo.stat().st_uid!=account.pw_uid:raise ValueError('Checkout must belong to the ordinary device user')
    config=load(CONFIG,{})
    if config and config.get('role')!=role:raise ValueError('Existing device role differs; refusing silent role change')
    missing=native_dependencies(offline or not config.get('enabled',True),role)
    destination=publish(sources)
    defaults={'schema':1,'role':role,'repo':str(repo),'user':user,'home':account.pw_dir,
              'enabled':True,'boot_standby':True,'interface':None,'commit':commit,'auto_update':role in ('pi','laptop'),'app_updates':True,'reboot_updates':True,'laptop_sleep':True,'laptop_sleep_delay':90,'pi_storage_client':True}
    defaults.update(config)
    if commit:defaults['commit']=commit
    installed=load(STATE/'installed.json',{})
    configuration_hash=settings_fingerprint(defaults)
    reconfigure=(not retry_missing or installed.get('configurationHash')!=configuration_hash or
        installed.get('version')!=destination.name or installed.get('commit')!=defaults['commit'])
    if role=='duet' and commit and not missing:advance_duet(defaults,commit)
    save(CONFIG,defaults)
    STATE.mkdir(parents=True,exist_ok=True)
    if role=='tiny':
        overlay=Path('/var/lib/car-dashboard/overrides');overlay.mkdir(parents=True,exist_ok=True)
        os.chown(overlay,account.pw_uid,account.pw_gid);overlay.chmod(0o700)
    if not Path('/etc/car-dashboard/update-link.json').exists():save('/etc/car-dashboard/update-link.json',{'home_url':'http://127.0.0.1:8791','device':'car-1'})
    if role=='duet':
        pub=Path(account.pw_dir)/'.ssh/drive-console-courier.pub'
        if pub.exists():
            key=pub.read_text().split()
            if len(key)<2 or key[0]!='ssh-ed25519':raise ValueError('Invalid courier public key')
            save(STATE/'allowed_signers',('duet '+key[0]+' '+key[1]+'\n').encode())
    if defaults.get('enabled',True) and shutil.which('timedatectl') and Path('/run/systemd/system').exists():
        # A numeric fallback lets an RTC-less device recover even when the wrong
        # clock prevents DNSSEC validation of the normal NTP pool hostname.
        save('/etc/systemd/timesyncd.conf.d/60-car-clock.conf',b'[Time]\nFallbackNTP=162.159.200.1 162.159.200.123\n')
        run(['timedatectl','set-ntp','true'],check=False)
    if not defaults.get('boot_standby',True) and role!='pi':
        standby=Path('/etc/car-dashboard/boot-standby.json')
        if standby.exists():
            previous=load(standby,{});previous['armed']=False;save(standby,previous)
    if not defaults.get('enabled',True):
        if Path('/run/systemd/system').exists():
            for service in ['car-home-tunnel.service','car-laptop-power.service','car-fleet.service','car-fleet-refresh.timer','car-update-link.service','car-radio-adapter.service','car-messages.service','car-telemetry.service','car-file-sync.service','car-data.service','car-storage-master.service','car-storage-chunks.service','car-storage-metadata.service','car-storage-mount.service','car-dashboard-update.timer','car-boot-standby.timer','car-wake-first.timer','car-wake-retry.timer','car-wake-status.service']:
                run(['systemctl','disable','--now',service],check=False)
        elif Path('/run/openrc').exists():
            for service in ['car-laptop-power','car-fleet','car-fleet-refresh','car-update-link','car-radio-adapter','car-messages','car-telemetry','car-file-sync','car-data','car-storage-master','car-storage-chunks','car-storage-metadata','car-storage-mount','car-fleet-boot','car-wake-status']:
                run(['rc-update','del',service,'default'],check=False);run(['rc-service',service,'stop'],check=False)
        return {'provisioning':VERSION,'disabled':True,'missing':missing}
    if Path('/run/systemd/system').exists():
        if reconfigure:systemd_units(role)
        if role=='tiny' and reconfigure:
            install_app_updates(defaults)
            install_device_tools(defaults)
            install_gps(defaults)
    elif Path('/run/openrc').exists():
        if reconfigure:openrc_units(role)
    else:raise ValueError('Automatic role setup needs systemd or OpenRC')
    install_pi_storage_policy(defaults)
    missing+=install_storage(defaults)
    save(STATE/'installed.json',{'provisioning':VERSION,'version':destination.name,'commit':defaults['commit'],'missing':missing,'configurationHash':configuration_hash})
    return {'provisioning':VERSION,'missing':missing,'role':role}


def git(config,*args,bare=None):
    command=['runuser','-u',config['user'],'--','git']
    command+=['--git-dir',str(bare)] if bare else ['-C',config['repo']]
    return run(command+list(args),timeout=180)


def source_bytes(config,commit,path,bare=None):
    command=['runuser','-u',config['user'],'--','git']
    command+=['--git-dir',str(bare)] if bare else ['-C',config['repo']]
    result=subprocess.run(command+['show',commit+':'+path],capture_output=True,timeout=180)
    if result.returncode:raise ValueError('Verified commit is missing a role source file')
    return result.stdout


def advance_duet(config,commit):
    old=git(config,'rev-parse','HEAD')
    if old==commit:return
    if git(config,'symbolic-ref','--short','HEAD')!='main' or git(config,'remote','get-url','origin')!=ORIGIN or git(config,'status','--porcelain'):
        raise ValueError('Duet checkout has local edits/branch/origin changes; preserving them')
    bare=Path(config['home'])/'.local/share/drive-console/courier/cache.git'
    git(config,'fetch','--no-tags',str(bare),commit)
    if git(config,'rev-parse','FETCH_HEAD')!=commit:raise ValueError('Duet fetched a different commit')
    git(config,'merge-base','--is-ancestor',old,commit)
    old_files=set(git(config,'ls-tree','-r','--name-only',old).splitlines())
    for name in git(config,'diff','--name-only',old,commit).splitlines():
        path=Path(config['repo'])/name
        if name not in old_files and (path.exists() or path.is_symlink()):raise ValueError('Duet update would replace a local file')
        for parent in path.parents:
            if parent==Path(config['repo']):break
            if parent.is_symlink():raise ValueError('Duet update would cross a local symlink')
    git(config,'merge','--ff-only',commit)
    tests=subprocess.run(['runuser','-u',config['user'],'--','/usr/bin/python3','-B','-m','unittest','discover','-s','tests'],cwd=config['repo'],capture_output=True,timeout=300)
    if tests.returncode:
        git(config,'reset','--keep',old);raise ValueError('Duet candidate tests failed; restored previous checkout')


def signed_prepared(config):
    state=Path(config['home'])/'.local/share/drive-console/courier'
    signers=STATE/'allowed_signers'
    if not signers.exists() or not (state/'ready.zip').exists():return None
    with zipfile.ZipFile(state/'ready.zip') as archive,tempfile.TemporaryDirectory() as temp:
        temp=Path(temp)
        for name in ('manifest.json','manifest.json.sig'):
            if archive.getinfo(name).file_size>16384:raise ValueError('Oversized courier metadata')
            (temp/name).write_bytes(archive.read(name))
        result=subprocess.run(['ssh-keygen','-Y','verify','-f',str(signers),'-I','duet','-n','car-dashboard-courier',
                               '-s',str(temp/'manifest.json.sig')],input=(temp/'manifest.json').read_bytes(),capture_output=True)
        if result.returncode:raise ValueError('Courier signature invalid; keeping installed role configuration')
        manifest=json.loads((temp/'manifest.json').read_text())
        if manifest.get('repository')!='TheRiddler08/Ford-Better-Head-Unit-' or manifest.get('branch')!='main':raise ValueError('Unexpected courier repository')
        return manifest['commit'],state/'cache.git'


def ensure_duet_supervisor(config):
    """Migrate an old running kiosk using only its own user's graphical context."""
    account=pwd.getpwnam(config['user']);base=Path(config['home'])/'.local/share/drive-console'
    state=Path(config['home'])/'.local/state/drive-console'
    known=load(state/'supervisor.json',{})
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            if proc.stat().st_uid!=account.pw_uid:continue
            argv=(proc/'cmdline').read_bytes().split(b'\0')
            if str(base/'launcher_lock.py').encode() not in argv:continue
            if known.get('pid')==int(proc.name) and known.get('version')==2 and known.get('source')==hashlib.sha256((base/'launcher_lock.py').read_bytes()).hexdigest():return True
            permitted={'DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','XAUTHORITY','XDG_SESSION_TYPE'}
            env={'HOME':config['home'],'USER':config['user'],'PATH':'/usr/local/bin:/usr/bin:/bin'}
            # Never log/dump the process environment or copy unrelated secrets.
            for entry in (proc/'environ').read_bytes().split(b'\0'):
                key,sep,value=entry.partition(b'=')
                if sep and key.decode(errors='ignore') in permitted:env[key.decode()]=value.decode()
            if not env.get('DISPLAY') and not env.get('WAYLAND_DISPLAY'):return False
            os.kill(int(proc.name),signal.SIGHUP)
            for _ in range(6):
                if not proc.exists():break
                time.sleep(1)
            command=['/usr/bin/python3',str(base/'launcher_lock.py'),str(base/'launch.sh')]
            if Path('/run/systemd/system').exists():
                args=['systemd-run','--collect','--unit=car-duet-display-'+str(account.pw_uid),'--uid='+config['user'],'--property=Restart=on-failure','--property=KillMode=control-group']
                for key,value in env.items():args+=['--setenv='+key+'='+value]
                run(args+command)
            else:
                subprocess.Popen(['runuser','-u',config['user'],'--',*command],env=env,start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return True
        except (OSError,ValueError):continue
    return False


def stage_duet_overlay(config,request):
    path=request['path'];ident=request['id']
    if path not in ('duet/proxy.py','duet/launcher_lock.py','duet/launch.sh') or not re.fullmatch('[0-9a-f]{16}',ident):raise ValueError('Invalid display activation')
    home=Path(config['home']);source=home/'.local/state/drive-console/software/overrides'/path
    if hashlib.sha256(source.read_bytes()).hexdigest()!=request['sha256']:raise ValueError('Display overlay changed before activation')
    code="""import json,os,shutil,sys,tempfile
from pathlib import Path
home,source,name,ident=sys.argv[1:];base=Path(home)/'.local/share/drive-console';backup=base/'duet-backups'/ident
backup.mkdir(parents=True,exist_ok=True)
for helper in ('proxy.py','launcher_lock.py','launch.sh'):
 if not (backup/helper).exists():shutil.copy2(base/helper,backup/helper)
with tempfile.NamedTemporaryFile(dir=base,delete=False) as f:f.write(Path(source).read_bytes());pending=Path(f.name)
pending.chmod(0o755 if name.endswith('.sh') else 0o644);os.replace(pending,base/name)
with tempfile.NamedTemporaryFile(mode='w',dir=base,delete=False) as f:
 json.dump(dict(id=ident,phase='pending',backup=str(backup)),f);f.flush();os.fsync(f.fileno());pending=Path(f.name)
os.replace(pending,base/'duet-activation.json')
"""
    marker=home/'.local/share/drive-console/duet-activation.json'
    current=load(marker,{})
    if current.get('phase')=='pending' and current.get('id')!=ident:return
    actual=home/'.local/share/drive-console'/Path(path).name
    if current.get('id')!=ident and hashlib.sha256(actual.read_bytes()).hexdigest()!=request.get('base'):
        save('/etc/car-dashboard/software-activation.json',{'id':ident,'phase':'failed','reason':'base-changed-before-activation','time':time.time()});return
    if current.get('id')!=ident:
        run(['runuser','-u',config['user'],'--','/usr/bin/python3','-c',code,str(home),str(source),Path(path).name,ident])


def rollback_duet_watchdog(config,activation):
    # This recovery code is independent of the candidate supervisor, which might
    # itself fail before it can run its normal rollback handler. All file work
    # runs as the ordinary display user, never candidate code as root.
    code="""import json,os,sys,tempfile,subprocess
from pathlib import Path
home,ident=sys.argv[1:];base=Path(home)/'.local/share/drive-console';marker=base/'duet-activation.json'
request=json.loads(marker.read_text());backup=Path(request['backup']).resolve()
if request['id']!=ident or not backup.is_relative_to((base/'duet-backups').resolve()):raise SystemExit(2)
for name in ('proxy.py','launcher_lock.py','launch.sh'):
 data=(backup/name).read_bytes()
 if name.endswith('.py'):compile(data,name,'exec')
 else:subprocess.run(['bash','-n'],input=data,check=True)
for name in ('proxy.py','launcher_lock.py','launch.sh'):
 with tempfile.NamedTemporaryFile(dir=base,delete=False) as f:f.write((backup/name).read_bytes());pending=Path(f.name)
 pending.chmod(0o755 if name.endswith('.sh') else 0o644);os.replace(pending,base/name)
request.update(phase='failed',reason='supervisor-watchdog')
with tempfile.NamedTemporaryFile(mode='w',dir=base,delete=False) as f:json.dump(request,f);f.flush();os.fsync(f.fileno());pending=Path(f.name)
os.replace(pending,marker)
"""
    run(['runuser','-u',config['user'],'--','/usr/bin/python3','-c',code,config['home'],activation['id']])
    if Path('/run/systemd/system').exists():
        unit='car-duet-display-'+str(pwd.getpwnam(config['user']).pw_uid)+'.service'
        run(['systemctl','reset-failed',unit],check=False);run(['systemctl','restart',unit],check=False)


def maintenance(config,update_locked=False):
    home=Path(config['home']);state=home/'.local/state/drive-console/software'
    request=load(state/'activation-request.json',{})
    if config['role']=='tiny':
        source=home/'.config/car-dashboard/ui-config.json'
        target=Path('/etc/car-dashboard/ui-config.json')
        environment=Path('/etc/car-dashboard/dashboard.env')
        custom=environment.exists() and any(line.startswith('CAR_UI_CONFIG=') for line in environment.read_text().splitlines())
        if source.exists() and not custom:
            desired=load(source,{})
            current=load(target,{})
            if type(desired.get('revision')) is int and desired['revision']>current.get('revision',-1):save(target,desired)
    previous=load('/etc/car-dashboard/software-activation.json',{})
    if request and request.get('id')!=previous.get('id'):
        path=request.get('path');ident=request.get('id')
        if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{16}',ident):raise ValueError('Invalid activation request identity')
        if config['role']=='duet':stage_duet_overlay(config,request)
        elif config['role']=='tiny' and path in ('recorder/obd_full_recorder.py','recorder/gps_recorder.py'):
            unit='car-obd-monitor.service' if path.endswith('obd_full_recorder.py') else 'car-gps-recorder.service'
            adopted=str(Path(config['repo'])/path) in run(['systemctl','cat',unit],check=False).stdout
            if adopted and run(['systemctl','is-active','--quiet',unit],check=False).returncode==0:
                overlay=Path('/var/lib/car-dashboard/overrides')/path
                staged=state/'staged-overrides'/path
                actual=overlay if overlay.is_file() else Path(config['repo'])/path
                if hashlib.sha256(actual.read_bytes()).hexdigest() not in (request.get('base'),request['sha256']):
                    save('/etc/car-dashboard/software-activation.json',{'id':ident,'phase':'failed','reason':'base-changed-before-activation','time':time.time()});return
                if hashlib.sha256(staged.read_bytes()).hexdigest()!=request['sha256']:raise ValueError('Recorder staging changed before activation')
                code="""import os,shutil,sys,tempfile
from pathlib import Path
source,target=map(Path,sys.argv[1:]);target.parent.mkdir(parents=True,exist_ok=True)
if target.exists() and target.read_bytes()!=source.read_bytes():shutil.copy2(target,target.with_suffix(target.suffix+'.previous'))
with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as f:f.write(source.read_bytes());f.flush();os.fsync(f.fileno());pending=Path(f.name)
pending.chmod(0o600);os.replace(pending,target)
"""
                run(['runuser','-u',config['user'],'--','/usr/bin/python3','-c',code,str(staged),str(overlay)])
                restart=run(['systemctl','restart',unit],check=False);time.sleep(3)
                success=restart.returncode==0 and run(['systemctl','is-active','--quiet',unit],check=False).returncode==0
                if not success:
                    overlay=Path('/var/lib/car-dashboard/overrides')/path
                    code="""import os,sys
from pathlib import Path
p=Path(sys.argv[1]);backup=p.with_suffix(p.suffix+'.previous')
if p.exists():os.replace(p,p.with_suffix(p.suffix+'.failed'))
if backup.exists():os.replace(backup,p)
"""
                    run(['runuser','-u',config['user'],'--','/usr/bin/python3','-c',code,str(overlay)])
                    run(['systemctl','restart',unit],check=False)
                save('/etc/car-dashboard/software-activation.json',{'id':ident,'phase':'applied' if success else 'failed','time':time.time()})
    if config['role']=='duet':
        marker=home/'.local/share/drive-console/duet-activation.json'
        activation=load(marker,{})
        if activation.get('phase')=='pending':
            attempt=load('/etc/car-dashboard/duet-activation-attempt.json',{})
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            same=attempt.get('id')==activation.get('id') and attempt.get('boot')==boot_id
            if same and time.monotonic()-attempt.get('monotonic',0)>65:
                rollback_duet_watchdog(config,activation)
            elif ensure_duet_supervisor(config) and not same:
                save('/etc/car-dashboard/duet-activation-attempt.json',{'id':activation['id'],'started':time.time(),'monotonic':time.monotonic(),'boot':boot_id})
        elif activation.get('phase') in ('applied','failed') and request.get('id')==activation.get('id'):
            target=home/'.local/share/drive-console'/Path(request['path']).name
            phase=activation['phase']
            if phase=='applied' and hashlib.sha256(target.read_bytes()).hexdigest()!=request['sha256']:phase='failed'
            save('/etc/car-dashboard/software-activation.json',{'id':request['id'],'phase':phase,'time':time.time()})
    # OS updates may require reboot, but receiving bytes is never a reboot signal.
    # A fresh root-owned real Park input is required; zero speed alone is not Park.
    flag=Path('/var/run/reboot-required')
    if flag.exists():
        power=Path('/run/car-dashboard/power-state.json')
        safe=False
        if config.get('reboot_updates',True) and power.exists():
            stat=power.stat();values=load(power,{})
            safe=stat.st_uid==0 and not stat.st_mode&0o022 and values.get('parked') is True and 0<=time.time()-values.get('checkedAt',0)<10
        save('/etc/car-dashboard/reboot-status.json',{'phase':'rebooting' if safe else 'pending-verified-park','time':time.time()})
        if safe:
            # These fixed role locks are also used by native/courier updates.
            if config['role']=='duet':
                paths=[home/'.local/share/drive-console/courier/prepare.lock',home/'.local/share/drive-console/courier/courier.lock']
            else:paths=[] if update_locked else [Path('/run/lock/car-dashboard-update.lock')]
            with ExitStack() as stack:
                for path in paths:
                    if config['role']=='duet':
                        try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
                        except FileNotFoundError:return
                        file=stack.enter_context(os.fdopen(fd,'rb'))
                    else:file=stack.enter_context(path.open('a'))
                    try:fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:
                        save('/etc/car-dashboard/reboot-status.json',{'phase':'pending-update-lock','time':time.time()});return
                if Path('/run/systemd/system').exists():run(['systemctl','reboot'])
                else:run(['reboot'])


def refresh(config):
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'refresh.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        if config['role']=='tiny':
            with Path('/run/lock/car-dashboard-update.lock').open('a') as update:
                try:fcntl.flock(update,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return
                maintenance(config,update_locked=True)
                return refresh_locked(config)
        maintenance(config)
        return refresh_locked(config)


def approved_root_source(config,commit):
    if commit==config.get('commit'):return True
    receipt=Path('/etc/car-dashboard/courier-last.json')
    if receipt.exists() and not receipt.is_symlink():
        stat=receipt.stat()
        if stat.st_uid==0 and not stat.st_mode&0o022 and load(receipt,{}).get('commit')==commit:return True
    # An ordinary-user checkout is not a root trust anchor. Check the advertised
    # main tip independently of that user's Git proxy/config/hooks/replacement
    # refs. If offline/private-repo access prevents this, keep the installed role
    # until a signed courier receipt or a later public-main check authorizes it.
    request=Request(ORIGIN+'/info/refs?service=git-upload-pack',headers={'Accept':'application/x-git-upload-pack-advertisement'})
    try:
        with urlopen(request,timeout=10) as response:raw=response.read(1048577)
        if len(raw)>1048576:return False
        match=re.search(rb'([0-9a-f]{40}) refs/heads/main(?:\x00|\n)',raw)
        return bool(match and match.group(1).decode()==commit)
    except (OSError,ValueError):return False


def refresh_locked(config):
    bare=None
    if config['role']=='duet':
        signed=signed_prepared(config)
        if not signed:return
        commit,bare=signed
    else:
        if config['role'] in ('pi','laptop') and config.get('auto_update',True):
            status=load(STATUS,{})
            stamp=STATE/'last-fetch.json';last=load(stamp,{}).get('time',0)
            if (status.get('wifiInternet') or config['role']=='laptop') and time.time()-last>=UPDATE_CHECK_INTERVAL:
                save(stamp,{'time':time.time()})
                if git(config,'remote','get-url','origin')!=ORIGIN:raise ValueError('Unexpected managed Git origin')
                if git(config,'symbolic-ref','--short','HEAD')!='main' or git(config,'status','--porcelain'):raise ValueError('Managed checkout has local changes/branch; keeping them')
                old=git(config,'rev-parse','HEAD')
                git(config,'fetch','--no-tags','origin','main')
                new=git(config,'rev-parse','FETCH_HEAD');git(config,'merge-base','--is-ancestor',old,new)
                if new!=old:
                    # Unchanged checks do not rerun tests or rewrite the checkout.
                    for source in SOURCES.values():ast.parse(git(config,'show',new+':'+source))
                    git(config,'merge','--ff-only',new)
                    tests=subprocess.run(['runuser','-u',config['user'],'--','/usr/bin/python3','-B','-m','unittest','discover','-s','tests'],cwd=config['repo'],capture_output=True,timeout=300)
                    if tests.returncode:
                        git(config,'reset','--keep',old);raise ValueError('Managed candidate tests failed; kept the previous version')
                save(stamp,{'time':time.time()})
        commit=git(config,'rev-parse','HEAD')
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Invalid source commit')
    if config['role']!='duet' and not approved_root_source(config,commit):raise ValueError('Checkout tip is not independently verified for root role updates')
    installed=load(STATE/'installed.json',{})
    configuration_hash=settings_fingerprint(config)
    if installed.get('commit')==commit and not installed.get('missing') and installed.get('configurationHash')==configuration_hash:return
    if config.get('commit'):git(config,'merge-base','--is-ancestor',config['commit'],commit,bare=bare)
    sources={name:source_bytes(config,commit,source,bare=bare) for name,source in SOURCES.items()}
    destination=publish(sources)
    args=['/usr/bin/python3','-I',str(destination/'fleet.py'),'install','--role',config['role'],'--repo',config['repo'],
          '--user',config['user'],'--source',str(destination),'--commit',commit,'--retry-missing']
    if config['role']=='tiny':args+=['--offline']
    subprocess.run(args,check=True,timeout=900)


def boot(config):
    current=Path(__file__).resolve().parent
    if config['role']=='laptop':return
    if config['role']!='pi':
        subprocess.run(['/usr/bin/python3','-I',str(current/'standby.py'),'boot'],check=False);return
    # OpenRC needs the same absolute boot deadlines as the systemd timers.
    for seconds in (30,150):
        while float(Path('/proc/uptime').read_text().split()[0])<seconds:time.sleep(.5)
        subprocess.run(['/usr/bin/python3','-I',str(current/'wake.py')],check=False)


def main():
    global STATUS
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install','run','refresh','refresh-loop','boot','status'])
    parser.add_argument('--role',choices=ADDRESSES);parser.add_argument('--repo');parser.add_argument('--user')
    parser.add_argument('--source');parser.add_argument('--commit');parser.add_argument('--offline',action='store_true')
    parser.add_argument('--retry-missing',action='store_true',help='Retry pending setup without restarting unchanged role services')
    args=parser.parse_args()
    settings=load(CONFIG,{})
    if settings.get('role')=='pi' and settings.get('pi_storage_client',True):
        STATUS=Path('/run/car-dashboard/fleet-status.json')
    if args.action=='status':print(json.dumps({'installed':load(STATE/'installed.json',{}),'status':load(STATUS,{}),'storage':load(STATE/'storage.json',{})},indent=2));return
    if os.geteuid()!=0:raise SystemExit('Use the device installer as your normal user; sudo is required once')
    if args.action=='install':
        if not all((args.role,args.repo,args.user,args.source)):raise ValueError('Install requires role/repo/user/source')
        sources={name:(Path(args.source)/name).read_bytes() for name in SOURCES}
        print(json.dumps(install(args.role,args.repo,args.user,sources,args.offline,args.commit,args.retry_missing)));return
    config=load(CONFIG,{})
    if config.get('role') not in ADDRESSES:raise ValueError('Install a device role first')
    if not config.get('enabled',True):return
    if args.action=='run':daemon(config)
    elif args.action=='refresh':refresh(config)
    elif args.action=='boot':boot(config)
    else:
        while True:
            subprocess.run(['/usr/bin/python3','-I',str(BASE/'fleet-current/fleet.py'),'refresh'],check=False)
            time.sleep(30)


if __name__=='__main__':main()
