#!/usr/bin/env python3
"""Share personal NetworkManager Wi-Fi profiles over pinned, restricted car SSH.

Passwords travel only on SSH stdin. They never enter argv, discovery, status,
radio, or the user-owned checkout. Enrollment is an explicit admin operation.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path('/etc/car-dashboard/wifi-sync')
FLEET=Path('/etc/car-dashboard/fleet.json')
STATUS=Path('/run/car-dashboard/wifi-sync-status.json')
PROFILES=Path('/etc/NetworkManager/system-connections')
RECEIVER=Path('/usr/local/sbin/car-wifi-receive')
PEERS={'pi':'172.16.42.3','tiny':'172.16.42.2','laptop':'172.16.42.4'}
LIMIT=65536


def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return {} if default is None else default


def atomic(path,data,mode=0o600):
    path=Path(path)
    if path.is_symlink():raise ValueError('Refusing symlinked Wi-Fi settings')
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as out:
        pending=Path(out.name)
        try:
            os.fchmod(out.fileno(),mode);out.write(data);out.flush();os.fsync(out.fileno())
            os.replace(pending,path)
        finally:pending.unlink(missing_ok=True)


def status(value):
    # Callers supply only fixed phase strings, role names and counts.
    STATUS.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
    STATUS.parent.chmod(0o755)
    atomic(STATUS,json.dumps(dict(value,updated=time.time())).encode(),0o644)


def nm(*args,secret=False):
    command=['nmcli','--terse','--escape','no']
    if secret:command+=['--show-secrets']
    result=subprocess.run(command+list(args),capture_output=True,timeout=15,env=dict(os.environ,LC_ALL='C.UTF-8'))
    if result.returncode:raise ValueError('NetworkManager operation failed')
    # Preserve SSID/passphrase spaces; never include command output in errors.
    return result.stdout.decode('utf-8').removesuffix('\n')


def field(ident,name,secret=False):return nm('-g',name,'connection','show','uuid',ident,secret=secret)


def validate(profile):
    if not isinstance(profile,dict) or set(profile)!={'ssid','security','psk','hidden'}:raise ValueError('Unsupported Wi-Fi profile')
    ssid=profile['ssid'];psk=profile['psk'];security=profile['security']
    if not isinstance(ssid,str) or not 1<=len(ssid.encode())<=32 or any(ord(c)<32 or ord(c)==127 for c in ssid):raise ValueError('Unsupported Wi-Fi name')
    if security not in ('wpa-psk','sae') or type(profile['hidden']) is not bool:raise ValueError('Unsupported Wi-Fi security')
    if not isinstance(psk,str) or any(ord(c)<32 or ord(c)==127 for c in psk):raise ValueError('Unsupported Wi-Fi credential')
    valid=(8<=len(psk.encode())<=63 or bool(re.fullmatch('[0-9a-fA-F]{64}',psk))) if security=='wpa-psk' else 1<=len(psk.encode())<=63
    if not valid:raise ValueError('Unsupported Wi-Fi credential')
    return profile


def identities(active=False):
    rows=[]
    for line in nm('-f','UUID,TYPE','connection','show',*(['--active'] if active else [])).splitlines():
        ident,_,kind=line.partition(':')
        if kind in ('802-11-wireless','wifi') and re.fullmatch('[0-9a-f-]{36}',ident):rows.append(ident)
    return rows


def export_profiles(user):
    rows=[]
    active=set(identities(active=True))
    for ident in sorted(identities(),key=lambda ident:ident not in active)[:128]:
        try:
            if field(ident,'802-11-wireless.mode') not in ('','infrastructure'):continue
            if field(ident,'connection.autoconnect')!='yes':continue
            permissions=field(ident,'connection.permissions')
            permitted={item.strip().rstrip(':') for item in re.split('[;,]',permissions)}
            if permissions not in ('','--') and 'user:'+user not in permitted:continue
            security=field(ident,'802-11-wireless-security.key-mgmt')
            if security not in ('wpa-psk','sae'):continue
            # Never prompt a desktop keyring or copy agent-owned/unsaved secrets.
            if field(ident,'802-11-wireless-security.psk-flags').split(' ',1)[0] not in ('','0'):continue
            row=validate({'ssid':field(ident,'802-11-wireless.ssid'),'security':security,
                'psk':field(ident,'802-11-wireless-security.psk',True),
                'hidden':field(ident,'802-11-wireless.hidden')=='yes'})
            if not any(p['ssid']==row['ssid'] and p['security']==row['security'] for p in rows):rows.append(row)
        except (ValueError,UnicodeError):continue
        if len(rows)==32:break
    return rows


def active_network_shared(rows):
    active=[]
    for ident in identities(active=True):
        if field(ident,'802-11-wireless.mode') in ('','infrastructure'):
            saved=field(ident,'802-11-wireless-security.psk-flags').split(' ',1)[0] in ('','0')
            ssid=field(ident,'802-11-wireless.ssid');security=field(ident,'802-11-wireless-security.key-mgmt')
            psk=field(ident,'802-11-wireless-security.psk',True) if saved and security in ('wpa-psk','sae') else None
            active.append(any(row['ssid']==ssid and row['security']==security and row['psk']==psk for row in rows))
    return all(active) if active else None


def escaped(value):
    # GLib keyfile escaping, including significant leading/trailing spaces.
    return value.replace('\\','\\\\').replace(' ','\\s').replace('\t','\\t').replace('\r','\\r').replace('\n','\\n')


def profile_path(row):
    ident=hashlib.sha256((row['ssid']+'\0'+row['security']).encode()).hexdigest()[:24]
    return PROFILES/('car-wifi-'+ident+'.nmconnection')


def keyfile(row):
    identity=uuid.uuid5(uuid.NAMESPACE_URL,'car-wifi:'+row['ssid']+'\0'+row['security'])
    ssid=';'.join(str(byte) for byte in row['ssid'].encode())+';'
    return (f'[connection]\nid={escaped(row["ssid"])} (car)\nuuid={identity}\ntype=wifi\n'
        'autoconnect=true\nautoconnect-priority=-20\n\n'
        f'[wifi]\nmode=infrastructure\nssid={ssid}\nhidden={str(row["hidden"]).lower()}\n\n'
        f'[wifi-security]\nkey-mgmt={row["security"]}\npsk={escaped(row["psk"])}\npsk-flags=0\n\n'
        '[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n').encode()


def try_autoconnect(rows):
    """Wake a disconnected NM Wi-Fi device, respecting radio/device opt-outs."""
    try:
        if not rows or nm('radio','wifi')!='enabled':return 'not-requested'
        for line in nm('-f','DEVICE,TYPE,STATE','device','status').splitlines():
            nic,kind,state=line.split(':',2)
            if kind!='wifi' or state!='disconnected':continue
            if nm('-g','GENERAL.AUTOCONNECT','device','show',nic)!='yes':continue
            visible=nm('-g','SSID','device','wifi','list','ifname',nic).splitlines()
            for row in rows:
                if row['ssid'] not in visible:continue
                ident=str(uuid.uuid5(uuid.NAMESPACE_URL,'car-wifi:'+row['ssid']+'\0'+row['security']))
                nm('--wait','12','connection','up','uuid',ident,'ifname',nic)
                return 'activation-requested'
        return 'waiting-for-wifi'
    except (ValueError,UnicodeError,subprocess.SubprocessError):return 'waiting-for-wifi'


def import_profiles(payload):
    config=read(FLEET)
    if config.get('role') not in PEERS or not config.get('enabled',True) or not config.get('wifi_sync',True):return {'phase':'disabled','imported':0}
    if not isinstance(payload,dict) or payload.get('schema')!=1 or set(payload)!={'schema','profiles'} or not isinstance(payload['profiles'],list) or len(payload['profiles'])>32:raise ValueError('Invalid Wi-Fi profile request')
    rows=[validate(p) for p in payload['profiles']]
    local={}
    for ident in identities():
        name=field(ident,'802-11-wireless.ssid')
        local.setdefault(name,[]).append(ident)
    imported=preserved=0;eligible=[]
    for row in rows:
        path=profile_path(row)
        managed_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'car-wifi:'+row['ssid']+'\0'+row['security']))
        # Retain every local profile. A lower-priority managed alternative can
        # recover a changed password without overwriting local credentials.
        # Explicit per-network autoconnect opt-outs still win.
        if any(field(ident,'connection.autoconnect')!='yes' for ident in local.get(row['ssid'],[])):
            preserved+=1;continue
        if any(ident!=managed_id for ident in local.get(row['ssid'],[])):preserved+=1
        eligible.append(row)
        content=keyfile(row)
        if path.is_symlink():raise ValueError('Refusing symlinked Wi-Fi profile')
        if path.exists() and path.read_bytes()==content:continue
        atomic(path,content);nm('connection','load',str(path));imported+=1
    # NetworkManager owns scans/autoconnect; never disconnect an existing uplink,
    # turn on a disabled radio, or copy the sender's IP/MAC/DNS configuration.
    result={'phase':'received','imported':imported,'preserved':preserved,'profiles':len(rows),'connection':try_autoconnect(eligible)}
    status(result);return result


def public_key(value):
    parts=value.strip().split()
    if len(parts)<2 or parts[0]!='ssh-ed25519' or not re.fullmatch('[A-Za-z0-9+/=]{40,120}',parts[1]):raise ValueError('Expected Ed25519 public key')
    import base64,struct
    try:
        raw=base64.b64decode(parts[1],validate=True)
        if raw!=struct.pack('>I',11)+b'ssh-ed25519'+struct.pack('>I',32)+raw[-32:] or len(raw)!=51:raise ValueError()
    except (ValueError,TypeError):raise ValueError('Invalid public key') from None
    return ' '.join(parts[:2])


def sender_key():
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700);ROOT.chmod(0o700)
    key=ROOT/'duet_ed25519'
    if key.is_symlink() or key.with_suffix('.pub').is_symlink():raise ValueError('Refusing symlinked pairing key')
    if not key.exists():subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    return public_key(key.with_suffix('.pub').read_text())


def install_wrapper(source):
    # Source is an immutable root-owned fleet version, or explicit admin bootstrap.
    body=('#!/bin/sh\nset -eu\n[ "$#" -eq 0 ] || exit 2\n'
        'exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/python3 -I '+str(source)+' receive\n')
    atomic(RECEIVER,body.encode(),0o755)


def authorize(user,key):
    account=pwd.getpwnam(user)
    if account.pw_uid==0 or not re.fullmatch('[a-zA-Z0-9_-]+',user):raise ValueError('Expected ordinary car account')
    if read(FLEET).get('role') not in PEERS:raise ValueError('Install the Pi/Tiny/laptop role first')
    key=public_key(key)
    directory=Path(account.pw_dir)/'.ssh';path=directory/'authorized_keys'
    if directory.is_symlink() or path.is_symlink():raise ValueError('Refusing symlinked SSH authorization')
    directory.mkdir(mode=0o700,exist_ok=True);os.chown(directory,account.pw_uid,account.pw_gid)
    lines=path.read_text().splitlines() if path.exists() else []
    expected='restrict,from="172.16.42.1",command="sudo -n /usr/local/sbin/car-wifi-receive" '+key+' car-wifi-duet'
    same=[line for line in lines if key.split()[1] in line.split()]
    if same and same!=[expected]:raise ValueError('Wi-Fi key already has different SSH permissions')
    rule=f'{user} ALL=(root) NOPASSWD: /usr/local/sbin/car-wifi-receive ""\n'
    with tempfile.NamedTemporaryFile(mode='w') as out:
        out.write(rule);out.flush()
        subprocess.run(['visudo','-cf',out.name],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    atomic('/etc/sudoers.d/car-wifi-sync',rule.encode(),0o440)
    if not same:atomic(path,('\n'.join(lines+[expected])+'\n').encode())
    os.chown(path,account.pw_uid,account.pw_gid);path.chmod(0o600)


def enroll(role,user,host_key):
    if read(FLEET).get('role')!='duet':raise ValueError('Run pairing on the Duet')
    if role not in PEERS or not re.fullmatch('[a-zA-Z0-9_-]+',user):raise ValueError('Invalid car peer')
    key=public_key(host_key);sender_key()
    peers=read(ROOT/'peers.json');peers[role]={'user':user,'hostKey':key}
    atomic(ROOT/'peers.json',json.dumps(peers).encode())
    atomic(ROOT/'known_hosts',''.join(PEERS[r]+' '+public_key(p['hostKey'])+'\n' for r,p in sorted(peers.items()) if r in PEERS).encode())


def sync_once():
    config=read(FLEET)
    if config.get('role')!='duet' or not config.get('enabled',True) or not config.get('wifi_sync',True):status({'phase':'disabled'});return
    peers=read(ROOT/'peers.json')
    if not peers:status({'phase':'pairing-required','peers':{}});return
    rows=export_profiles(config['user'])
    active_shared=active_network_shared(rows)
    if not rows:
        status({'phase':'profiles-unavailable','profiles':0,'activeNetworkShared':active_shared,
            'detail':'Save personal Wi-Fi passwords for system use in Duet NetworkManager settings'});return
    payload=json.dumps({'schema':1,'profiles':rows}).encode()
    if len(payload)>LIMIT:raise ValueError('Wi-Fi profile set exceeds limit')
    states={}
    for role,peer in sorted(peers.items()):
        if role not in PEERS or not re.fullmatch('[a-zA-Z0-9_-]+',peer.get('user','')):continue
        command=['ssh','-F','/dev/null','-T','-b','172.16.42.1','-i',str(ROOT/'duet_ed25519'),
            '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
            '-o','UserKnownHostsFile='+str(ROOT/'known_hosts'),'-o','ConnectTimeout=4',
            '-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2',peer['user']+'@'+PEERS[role]]
        try:
            result=subprocess.run(command,input=payload,capture_output=True,timeout=25)
            reply=json.loads(result.stdout) if not result.returncode and len(result.stdout)<=4096 else {}
            phase=reply.get('phase')
            states[role]={'phase':phase if phase in ('received','disabled') else 'unreachable','profiles':len(rows)}
        except (OSError,ValueError,subprocess.SubprocessError):states[role]={'phase':'unreachable','profiles':len(rows)}
    phase='ready' if states and all(s['phase']=='received' for s in states.values()) else 'waiting-peers'
    if active_shared is False:phase='active-profile-unavailable'
    status({'phase':phase,'profiles':len(rows),'activeNetworkShared':active_shared,'peers':states})


def pair(role,user):
    config=read(FLEET)
    if config.get('role')!='duet' or role not in PEERS or not re.fullmatch('[a-zA-Z0-9_-]+',user or ''):raise ValueError('Pair on the Duet with a fixed car peer')
    admin=pwd.getpwnam(config['user'])
    if not admin.pw_uid:raise ValueError('Expected ordinary Duet administrator')
    target=user+'@'+PEERS[role]
    with tempfile.TemporaryDirectory(prefix='car-wifi-pair-') as directory:
        os.chown(directory,admin.pw_uid,admin.pw_gid)
        control=Path(directory)/'ssh'
        ssh=['runuser','-u',config['user'],'--','ssh','-o','IdentityAgent=none',
            '-o','ControlMaster=auto','-o','ControlPath='+str(control),'-o','ControlPersist=120',
            '-o','BatchMode=no','-o','ConnectTimeout=10']
        identity=os.environ.get('CAR_ADMIN_IDENTITY')
        if identity:
            if not Path(identity).is_file():raise ValueError('Administrative identity file missing')
            ssh+=['-o','IdentitiesOnly=yes','-i',identity]
        else:ssh+=['-o','PubkeyAuthentication=no','-o','PreferredAuthentications=keyboard-interactive,password']
        try:
            print('Pairing '+role+' over car Ethernet. Use its normal SSH and sudo login once.',flush=True)
            host=subprocess.run([*ssh,target,'cat /etc/ssh/ssh_host_ed25519_key.pub'],stdout=subprocess.PIPE,check=True,timeout=180).stdout.decode()
            host=public_key(host)  # Obtained through the verified admin SSH session.
            copy='umask 077; mkdir -p "$HOME/car-wifi-bootstrap" && cat > "$HOME/car-wifi-bootstrap/wifi_profiles.py"'
            subprocess.run([*ssh,target,copy],input=Path(__file__).read_bytes(),check=True,timeout=30)
            command="printf '%s\\n' "+shlex.quote(sender_key())+' | sudo /usr/bin/python3 "$HOME/car-wifi-bootstrap/wifi_profiles.py" authorize --user '+shlex.quote(user)
            subprocess.run([*ssh,'-t',target,command],check=True,timeout=180)
            enroll(role,user,host);sync_once()
            print('Initial Wi-Fi transfer: '+read(STATUS).get('peers',{}).get(role,{}).get('phase','pending'),flush=True)
            print('Paired. Saved personal Wi-Fi networks will be shared automatically every 30 seconds.',flush=True)
        finally:
            subprocess.run([*ssh,'-O','exit',target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('watch','sync','receive','key','enroll','authorize','pair'))
    parser.add_argument('--role',choices=PEERS);parser.add_argument('--user');args=parser.parse_args()
    if os.geteuid()!=0:raise ValueError('This helper runs through the installed car service')
    if args.action=='pair':pair(args.role,args.user);return
    if args.action=='key':print(sender_key());return
    if args.action=='enroll':enroll(args.role,args.user,sys.stdin.read(4096));return
    if args.action=='authorize':
        # One explicit admin bootstrap, before the peer has Internet. Later fleet
        # installs replace this wrapper with their immutable verified version.
        source=Path('/usr/local/lib/car-dashboard/wifi-bootstrap.py')
        atomic(source,Path(__file__).read_bytes(),0o644);install_wrapper(source)
        authorize(args.user,sys.stdin.read(4096));return
    if args.action=='receive':
        raw=sys.stdin.buffer.read(LIMIT+1)
        if len(raw)>LIMIT:raise ValueError('Wi-Fi request exceeds limit')
        with open('/run/lock/car-wifi-receive.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);print(json.dumps(import_profiles(json.loads(raw))))
        return
    if args.action=='sync':sync_once();return
    while True:
        started=time.monotonic()
        try:sync_once()
        except (OSError,ValueError,subprocess.SubprocessError):status({'phase':'retrying','detail':'Wi-Fi profile sharing is waiting for NetworkManager or a paired peer'})
        time.sleep(max(1,30-(time.monotonic()-started)))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,subprocess.SubprocessError):
        print('Wi-Fi sharing could not complete; check role, pairing and NetworkManager.',file=sys.stderr);sys.exit(1)
