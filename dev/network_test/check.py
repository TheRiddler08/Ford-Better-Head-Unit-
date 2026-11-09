"""Actual packet/SSH/NM checks, confined to this disposable Docker container."""
import json,os,subprocess,sys,time
from pathlib import Path
if os.environ.get('CAR_NETWORK_TEST')!='1' or not Path('/.dockerenv').exists():
    raise SystemExit('Use bash dev/check-car-network.sh; this test needs its disposable container.')
sys.path.insert(0,'/app')
from deploy import fleet,native_firewall,wifi_profiles as wifi

def run(*args,input=None,ok=True):
    result=subprocess.run(list(args),input=input,capture_output=True,timeout=25)
    if ok and result.returncode:raise RuntimeError(result.stderr.decode()[-2000:])
    return result
def child():
    process=subprocess.Popen(['unshare','--net','sleep','infinity'])
    before=os.readlink('/proc/self/ns/net')
    for _ in range(100):
        if os.readlink(f'/proc/{process.pid}/ns/net')!=before:return process
        time.sleep(.02)
    raise RuntimeError('Test network namespace not ready')
client=child();wan=child()
def peer(process,*args,**kw):return run('nsenter','-t',str(process.pid),'-n',*args,**kw)
for proc,nic,other,ip,remote in [(client,'eth0','car-client','172.16.42.1','172.16.42.2'),(wan,'wlan0','car-wan','192.0.2.1','192.0.2.2')]:
    run('ip','link','add',nic,'type','veth','peer','name',other)
    run('ip','link','set',other,'netns',str(proc.pid))
    run('ip','addr','add',ip+'/24','dev',nic);run('ip','link','set',nic,'up')
    peer(proc,'ip','link','set','lo','up');peer(proc,'ip','link','set',other,'name','eth0')
    peer(proc,'ip','addr','add',remote+'/24','dev','eth0');peer(proc,'ip','link','set','eth0','up')
peer(client,'ip','address','add','172.16.42.3/24','dev','eth0')
peer(client,'ip','route','add','default','via','172.16.42.1')
subprocess.Popen(['nsenter','-t',str(wan.pid),'-n','python3','-m','http.server','8000'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
baseline=b'''table inet filter {
chain input { type filter hook input priority filter; policy drop; iifname "lo" accept; ct state established,related accept; tcp dport 22 accept; }
chain forward { type filter hook forward priority filter; policy drop; ct state established,related accept; }
}
table ip nat {
chain POSTROUTING { type nat hook postrouting priority srcnat; policy accept; meta mark 0x42 masquerade; }
chain CAR_FLEET_NAT { ip saddr 172.16.42.0/24 oifname "wlan0" masquerade; }
}
'''
run('nft','-f','-',input=baseline)
request=['curl','--noproxy','*','--connect-timeout','1','--max-time','2','-fsS','http://192.0.2.2:8000/']
assert peer(client,*request,ok=False).returncode!=0
run('iptables','-P','FORWARD','DROP')
sharing=fleet.Sharing();sharing.firewall('eth0','wlan0')
try:peer(client,*request)
except Exception:
    print(run('nft','list','ruleset').stdout.decode(),flush=True)
    print(run('ip','netconf','show').stdout.decode(),flush=True)
    raise
assert not native_firewall.plan(json.loads(run('nft','-j','list','ruleset').stdout),'eth0','wlan0')
print('PASS: real client packets traverse repaired stacked DROP chains and scoped NAT',flush=True)
run('nft','delete','table','inet','filter');run('nft','-f','-',input=baseline.split(b'table ip nat')[0])
sharing.firewall('eth0','wlan0');peer(client,*request)
print('PASS: actual native firewall reload is repaired without changing its DROP policy',flush=True)

# Real dnsmasq through repaired UDP/TCP input rules and the selected uplink.
dns_server='''import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('192.0.2.2',5353))
while True:
 data,address=s.recvfrom(4096);s.sendto(data[:2]+b'\\x81\\x83'+data[4:6]+b'\\0'*6+data[12:],address)
'''
subprocess.Popen(['nsenter','-t',str(wan.pid),'-n','python3','-c',dns_server],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
subprocess.Popen(['dnsmasq','--keep-in-foreground','--conf-file=/dev/null','--bind-interfaces','--no-resolv','--listen-address=172.16.42.1','--server=192.0.2.2#5353@wlan0'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(.3)
peer(client,'python3','-c',"import socket; q=b'\\x12\\x34\\x01\\x00\\x00\\x01'+b'\\0'*6+b'\\x04test\\x07invalid\\0\\0\\1\\0\\1'; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(3);s.sendto(q,('172.16.42.1',53));d=s.recv(4096);assert d[:2]==q[:2] and d[3]&15==3")
print('PASS: client DNS reaches real dnsmasq and the selected upstream',flush=True)

# Real NetworkManager and the restricted receiver; the namespace peers share
# only this disposable filesystem, not the cloud host's configuration.
wifi.FLEET.parent.mkdir(exist_ok=True);wifi.FLEET.write_text('{"role":"duet","user":"root"}')
public=wifi.sender_key()
run('ssh-keygen','-A');host=Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text()
wifi.enroll('pi','car',host)
wifi.FLEET.write_text('{"role":"pi"}')
Path('/run/sshd').mkdir(exist_ok=True)
Path('/etc/NetworkManager/conf.d/99-test.conf').write_text('[keyfile]\nunmanaged-devices=interface-name:*\n')
run('useradd','-m','car');run('usermod','-p','*','car')
run('dbus-daemon','--system','--fork')
subprocess.Popen(['NetworkManager','--no-daemon','--log-level=ERR'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
run('python3','/app/deploy/wifi_profiles.py','authorize','--user','car',input=public.encode())
peer(client,'/usr/sbin/sshd')
for _ in range(100):
    if run('nmcli','general','status',ok=False).returncode==0:break
    time.sleep(.05)
ssh=['ssh','-F','/dev/null','-T','-b','172.16.42.1','-i',str(wifi.ROOT/'duet_ed25519'),
    '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
    '-o','UserKnownHostsFile='+str(wifi.ROOT/'known_hosts'),'car@172.16.42.3']
profile={'ssid':' Test; Home ☃ ','security':'wpa-psk','psk':' test $() ; \\ spaces ','hidden':False}
response=run(*ssh,input=json.dumps({'schema':1,'profiles':[profile]}).encode())
assert json.loads(response.stdout)['phase']=='received'
rows=wifi.export_profiles('car')
if rows!=[profile]:
    print('Export count:',len(rows),flush=True)
    for ident in wifi.identities():
        for field in ['connection.autoconnect','connection.permissions','802-11-wireless.mode','802-11-wireless-security.key-mgmt','802-11-wireless-security.psk-flags']:
            print(field,repr(wifi.field(ident,field)),flush=True)
        for name,key in [('802-11-wireless.ssid','ssid'),('802-11-wireless-security.psk','psk')]:
            value=wifi.field(ident,name,key=='psk');print(key,'length',len(value),'matches',value==profile[key],flush=True)
assert rows==[profile], 'NetworkManager must round-trip every credential byte'
assert wifi.profile_path(profile).stat().st_mode&0o777==0o600
denied=run(*ssh,'touch /tmp/should-not-exist',input=b'{"schema":1,"profiles":[]}')
assert json.loads(denied.stdout)['phase']=='received' and not Path('/tmp/should-not-exist').exists()
old=(wifi.ROOT/'known_hosts').read_bytes();(wifi.ROOT/'known_hosts').write_text('172.16.42.3 '+public+'\n')
assert run(*ssh,input=b'{"schema":1,"profiles":[]}',ok=False).returncode!=0
(wifi.ROOT/'known_hosts').write_bytes(old)
print('PASS: pinned restricted SSH imports real NetworkManager profiles, keeps special characters and refuses shell execution/changed host keys',flush=True)
