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
import ipaddress
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

ADDRESSES={'duet':'172.16.42.1','tiny':'172.16.42.2','pi':'172.16.42.3'}
SUBNET='172.16.42.0/24'
BROADCAST='172.16.42.255'
PORT=8798
VERSION=1
BASE=Path('/usr/local/lib/car-dashboard')
CONFIG=Path('/etc/car-dashboard/fleet.json')
STATE=Path('/var/lib/car-dashboard-fleet')
STATUS=Path('/etc/car-dashboard/fleet-status.json')
ORIGIN='https://github.com/TheRiddler08/Ford-Better-Head-Unit-.git'
SOURCES={'fleet.py':'deploy/fleet.py','wake.py':'power/wake.py','standby.py':'power/standby.py'}
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
    candidates={role} if online and role in ('pi','duet') else set()
    for name,peer in peers.items():
        if name in ('pi','duet') and peer.get('internet') is True and 0<=now-peer.get('seen',0)<=15:candidates.add(name)
    return next((name for name in ('pi','duet') if name in candidates),None)


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
        result=run(['curl','--ipv4','--interface',nic,'--head','--silent','--show-error','--output','/dev/null',
                    '--connect-timeout','3','--max-time','5','https://github.com/'],timeout=7,check=False)
        if result.returncode==0:
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
    def __init__(self):self.gateway_state=None;self.route_state=None;self.dns=None
    def firewall(self,lan,wan):
        # Scope all rules to our chains and car subnet. No flush ruleset, global
        # DROP policy changes, DHCP service, or edits to Docker/Tailscale chains.
        for table,chain in [('filter','CAR_FLEET_FWD'),('nat','CAR_FLEET_NAT')]:
            run(['iptables','-w','5','-t',table,'-N',chain],check=False)
            run(['iptables','-w','5','-t',table,'-F',chain])
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
    def route(self,nic,gateway):
        desired=(nic,gateway)
        if desired==self.route_state:return
        previous=self.route_state[0] if self.route_state else nic
        removed=run(['ip','-4','route','del','default','dev',previous,'proto','242','metric','50'],check=False)
        if removed.returncode==0 or (self.route_state and self.route_state[1]):
            if shutil.which('resolvectl'):run(['resolvectl','revert',previous],check=False)
            else:run(['nmcli','device','modify',previous,'ipv4.dns','','ipv4.dns-priority','0'],check=False)
        if gateway:
            run(['ip','-4','route','replace','default','via',gateway,'dev',nic,'proto','242','metric','50'])
            if shutil.which('resolvectl'):
                run(['resolvectl','dns',nic,gateway]);run(['resolvectl','domain',nic,'~.']);run(['resolvectl','default-route',nic,'yes'])
            else:run(['nmcli','device','modify',nic,'ipv4.dns',gateway,'ipv4.ignore-auto-dns','yes','ipv4.dns-priority','-50'])
        self.route_state=desired
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
        if self.gateway_state:self.firewall(self.gateway_state[0],None)
        if self.route_state:self.route(self.route_state[0],None)


def write_wake(config,nic,peers):
    role=config['role']
    if role=='pi':
        rows=[{'name':name,'mac':peer['mac'],'broadcast':BROADCAST,'interface':nic}
              for name,peer in sorted(peers.items()) if name in ('tiny','duet')]
        save('/etc/car-dashboard/pi-wake.json',{'schema':1,'status':{'bind':ADDRESSES['pi'],'interface':nic},'targets':rows})
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
                save(STATE/'peers.json',peers)
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
    if role in ('duet','tiny') and Path('/run/openrc').exists():needed.append('loginctl')
    missing=[name for name in needed if not shutil.which(name)]
    if not missing or offline:return missing
    if shutil.which('apt-get'):
        run(['apt-get','update'],timeout=300)
        env=dict(os.environ,DEBIAN_FRONTEND='noninteractive')
        subprocess.run(['apt-get','install','-y','network-manager','iproute2','iptables','dnsmasq-base','curl','ethtool'],env=env,check=True,timeout=600)
    elif shutil.which('apk'):
        run(['apk','add','networkmanager','iproute2','iptables','dnsmasq','curl','ethtool','elogind'],timeout=600)
    return [name for name in needed if not shutil.which(name)]


def unit(name,content):save(Path('/etc/systemd/system')/name,content.encode())


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
OnCalendar=*-*-* *:0/15:00
RandomizedDelaySec=60
Persistent=true
[Install]
WantedBy=timers.target
''')
    run(['systemctl','daemon-reload'])
    disabled=Path('/etc/car-dashboard/updates-disabled').exists() or not config.get('app_updates',True)
    run(['systemctl','disable' if disabled else 'enable','--now','car-dashboard-update.timer'])


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
    else:
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
    run(['systemctl','daemon-reload'])
    run(['systemctl','disable','--now','car-duet-ethernet-'+role+'.timer'],check=False)
    run(['systemctl','enable','--now','car-fleet.service','car-fleet-refresh.timer'])
    # Never force suspend the SSH session that installed an update. Arming is
    # automatic, but the once-per-boot timer starts on the next device boot.
    extras=['car-wake-first.timer','car-wake-retry.timer','car-wake-status.service'] if role=='pi' else ['car-boot-standby.timer']
    run(['systemctl','enable',*extras])
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


def install(role,repo,user,sources,offline=False,commit=None):
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
              'enabled':True,'boot_standby':True,'interface':None,'commit':commit,'auto_update':role=='pi','app_updates':True}
    defaults.update(config)
    if commit:defaults['commit']=commit
    save(CONFIG,defaults)
    STATE.mkdir(parents=True,exist_ok=True)
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
            for service in ['car-fleet.service','car-fleet-refresh.timer','car-dashboard-update.timer','car-boot-standby.timer','car-wake-first.timer','car-wake-retry.timer','car-wake-status.service']:
                run(['systemctl','disable','--now',service],check=False)
        elif Path('/run/openrc').exists():
            for service in ['car-fleet','car-fleet-refresh','car-fleet-boot','car-wake-status']:
                run(['rc-update','del',service,'default'],check=False);run(['rc-service',service,'stop'],check=False)
        return {'provisioning':VERSION,'disabled':True,'missing':missing}
    if Path('/run/systemd/system').exists():
        systemd_units(role)
        if role=='tiny':install_app_updates(defaults)
    elif Path('/run/openrc').exists():openrc_units(role)
    else:raise ValueError('Automatic role setup needs systemd or OpenRC')
    save(STATE/'installed.json',{'provisioning':VERSION,'version':destination.name,'commit':defaults['commit'],'missing':missing})
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


def refresh(config):
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'refresh.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        if config['role']=='tiny':
            with Path('/run/lock/car-dashboard-update.lock').open('a') as update:
                try:fcntl.flock(update,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return
                return refresh_locked(config)
        return refresh_locked(config)


def refresh_locked(config):
    bare=None
    if config['role']=='duet':
        signed=signed_prepared(config)
        if not signed:return
        commit,bare=signed
    else:
        if config['role']=='pi' and config.get('auto_update',True):
            status=load(STATUS,{})
            stamp=STATE/'last-fetch.json';last=load(stamp,{}).get('time',0)
            if status.get('wifiInternet') and time.time()-last>900:
                if git(config,'remote','get-url','origin')!=ORIGIN:raise ValueError('Unexpected Pi Git origin')
                if git(config,'symbolic-ref','--short','HEAD')!='main' or git(config,'status','--porcelain'):raise ValueError('Pi checkout has local changes/branch; keeping them')
                old=git(config,'rev-parse','HEAD')
                git(config,'fetch','--no-tags','origin','main')
                new=git(config,'rev-parse','FETCH_HEAD');git(config,'merge-base','--is-ancestor',old,new)
                # Validate candidate syntax before advancing the managed checkout.
                for source in SOURCES.values():ast.parse(git(config,'show',new+':'+source))
                git(config,'merge','--ff-only',new)
                tests=subprocess.run(['runuser','-u',config['user'],'--','/usr/bin/python3','-B','-m','unittest','discover','-s','tests'],cwd=config['repo'],capture_output=True,timeout=300)
                if tests.returncode:
                    git(config,'reset','--keep',old);raise ValueError('Pi candidate tests failed; kept the previous version')
                save(stamp,{'time':time.time()})
        commit=git(config,'rev-parse','HEAD')
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Invalid source commit')
    installed=load(STATE/'installed.json',{})
    if installed.get('commit')==commit and not installed.get('missing'):return
    if config.get('commit'):git(config,'merge-base','--is-ancestor',config['commit'],commit,bare=bare)
    sources={name:source_bytes(config,commit,source,bare=bare) for name,source in SOURCES.items()}
    destination=publish(sources)
    args=['/usr/bin/python3','-I',str(destination/'fleet.py'),'install','--role',config['role'],'--repo',config['repo'],
          '--user',config['user'],'--source',str(destination),'--commit',commit]
    if config['role']=='tiny':args+=['--offline']
    subprocess.run(args,check=True,timeout=900)


def boot(config):
    current=Path(__file__).resolve().parent
    if config['role']!='pi':
        subprocess.run(['/usr/bin/python3','-I',str(current/'standby.py'),'boot'],check=False);return
    # OpenRC needs the same absolute boot deadlines as the systemd timers.
    for seconds in (30,150):
        while float(Path('/proc/uptime').read_text().split()[0])<seconds:time.sleep(.5)
        subprocess.run(['/usr/bin/python3','-I',str(current/'wake.py')],check=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install','run','refresh','refresh-loop','boot','status'])
    parser.add_argument('--role',choices=ADDRESSES);parser.add_argument('--repo');parser.add_argument('--user')
    parser.add_argument('--source');parser.add_argument('--commit');parser.add_argument('--offline',action='store_true')
    args=parser.parse_args()
    if args.action=='status':print(json.dumps({'installed':load(STATE/'installed.json',{}),'status':load(STATUS,{})},indent=2));return
    if os.geteuid()!=0:raise SystemExit('Use the device installer as your normal user; sudo is required once')
    if args.action=='install':
        if not all((args.role,args.repo,args.user,args.source)):raise ValueError('Install requires role/repo/user/source')
        sources={name:(Path(args.source)/name).read_bytes() for name in SOURCES}
        print(json.dumps(install(args.role,args.repo,args.user,sources,args.offline,args.commit)));return
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
