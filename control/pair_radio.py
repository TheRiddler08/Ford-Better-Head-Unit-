"""One-time Tiny pairing over authenticated SSH; no keys/tokens printed.

Run as the installed ordinary Tiny user. Existing pins and credentials are kept;
changed pins fail closed. sudo is used only to install configuration as data and
restart the already verified normal role reconciler, never to run this module.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
from control.update_agent import Agent, atomic


REMOTE = r'''
import json, os, re, shlex, sys
from pathlib import Path
public=sys.argv[1].split()
if len(public)!=2 or public[0]!='ssh-ed25519' or not re.fullmatch('[A-Za-z0-9+/=]+',public[1]):
    raise SystemExit('Invalid tunnel public key')
env=Path.home()/'.config/car-home/control.env'
token=None
for line in env.read_text().splitlines():
    name,sep,value=line.partition('=')
    if sep and name=='CAR_DEVICE_TOKEN':
        words=shlex.split(value,comments=False,posix=True)
        if len(words)!=1:raise SystemExit('Invalid device credential')
        token=words[0]
if not token or len(token)<32:raise SystemExit('Run the normal home installer first')
folder=Path.home()/'.ssh'
if folder.is_symlink():raise SystemExit('Refusing symlinked SSH directory')
folder.mkdir(mode=0o700,exist_ok=True)
path=folder/'authorized_keys'
if path.is_symlink():raise SystemExit('Refusing symlinked authorized_keys')
entry='restrict,port-forwarding,permitopen="127.0.0.1:8790",command="/bin/false" '+' '.join(public)
existing=path.read_text() if path.exists() else ''
matches=[line for line in existing.splitlines() if public[1] in line.split()]
if matches and matches!=[entry]:raise SystemExit('Tunnel key already has different restrictions; refusing to change them')
if not matches:
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
    with os.fdopen(fd,'w') as file:
        file.write(('\n' if existing and not existing.endswith('\n') else '')+entry+'\n')
        file.flush();os.fsync(file.fileno())
path.chmod(0o600)
print(json.dumps({'deviceToken':token}))
'''


def target(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*',value):
        raise ValueError('Use an explicit user@hostname or user@IPv4 home SSH target')
    return value


def step(label, command, timeout=30, capture=False, check=True):
    print(label+'…',flush=True)
    try:
        return subprocess.run(command,timeout=timeout,capture_output=capture,
                              text=capture,check=check)
    except subprocess.TimeoutExpired as exc:
        # Never print captured output: an SSH response may contain a device token.
        raise ValueError(f'{label} timed out after {timeout} seconds. Pairing is incomplete; no radio test result is implied.') from exc


def home_credentials(host, public):
    command=['ssh','-o','StrictHostKeyChecking=ask','-o','ConnectTimeout=10',
             '-o','ConnectionAttempts=1','-o','ServerAliveInterval=10',
             '-o','ServerAliveCountMax=2','-o','NumberOfPasswordPrompts=1',
             '-o','ControlMaster=no','-o','ControlPath=none',target(host),
             shlex.join(['python3','-c',REMOTE,public])]
    result=step('Connecting to home and enrolling the restricted tunnel key',command,
                timeout=120,capture=True,check=False)
    if result.returncode:
        raise ValueError('Home pairing failed: '+result.stderr[-500:].strip())
    token=json.loads(result.stdout)['deviceToken']
    if not isinstance(token,str) or len(token)<32 or '\n' in token:
        raise ValueError('Invalid credential returned by home')
    return token


def merge_options(existing, host, device):
    from control.rak3172 import adapter_command
    if existing.get('radio_adapter'):
        raise ValueError('An explicit radio_adapter is already configured; preserve it and review pairing manually')
    result=dict(existing,home_url='http://127.0.0.1:8791',home_ssh_target=target(host),
                radio_model='RAK3172',radio_device=device)
    # Keep explicit opt-outs/budgets; only seed missing settings.
    for name,value in {'radio_fport':10,'max_app_bytes':11,'uplink_spacing':60,
                       'uplinks_per_day':120,'radio_self_test':True}.items():
        result.setdefault(name,value)
    adapter_command(result)
    return result


def key_pair(path):
    path=Path(path)
    if path.is_symlink():raise ValueError('Refusing symlinked tunnel key')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if not path.exists():
        step('Creating the dedicated forwarding key',['ssh-keygen','-q','-t','ed25519','-N','','-C','car-home-forward-only','-f',str(path)])
    path.chmod(0o600)
    # Derive from the actual private key; do not trust a mismatched .pub file.
    result=step('Checking the forwarding key',['ssh-keygen','-y','-P','','-f',str(path)],capture=True)
    words=result.stdout.split()
    if len(words)<2 or words[0]!='ssh-ed25519':raise ValueError('Unsupported tunnel key')
    return ' '.join(words[:2])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',required=True,type=target)
    parser.add_argument('--device',required=True)
    args=parser.parse_args()
    if os.geteuid()==0:raise ValueError('Run as the ordinary installed Tiny user, not with sudo')
    config=json.loads(Path('/etc/car-dashboard/fleet.json').read_text())
    if config['role']!='tiny' or Path(config['home']).resolve()!=Path.home().resolve():
        raise ValueError('Run on the installed car Tiny as its dashboard user')
    path=Path('/etc/car-dashboard/update-link.json')
    if path.is_symlink():raise ValueError('Refusing symlinked link configuration')
    existing=json.loads(path.read_text()) if path.exists() else {}
    options=merge_options(existing,args.home,args.device)
    # Service supplemental dialout access need not be in the current SSH login's
    # groups. Probe as the ordinary user with dialout, never as root.
    active=step('Checking the Tiny radio service',['systemctl','is-active','--quiet','car-radio-adapter.service'],check=False).returncode==0
    step('Releasing the serial radio for read-only checks',['sudo','systemctl','stop','car-radio-adapter.service'],timeout=60)
    try:
        probe=step('Reading RAK3172 firmware and join status',['sudo','-u',config['user'],'-g','dialout','/usr/bin/python3',
             '-m','control.rak3172','--device',args.device,'--probe'],timeout=60,capture=True,check=False)
        if probe.returncode:raise ValueError('Radio probe failed: '+probe.stderr[-500:].strip())
        status=json.loads(probe.stdout)['status']
    finally:
        if active:step('Restoring the Tiny radio service',['sudo','systemctl','start','car-radio-adapter.service'],timeout=60)
    if status['nwm']!='1' or status['band']!='5' or status['njm']!='1' or status['class']!='A' or status['njs']!='1':
        raise ValueError('Radio must already be provisioned and joined in US915 OTAA Class A; no radio settings were changed')
    if not isinstance(status.get('deveui'),str) or status['deveui'].lower()!='ac1f09fffe29d8b2':
        raise ValueError('This pairing command expects the already commissioned vehicle DevEUI; no configuration changed')
    credentials=Path.home()/'.config/car-dashboard'
    credential=credentials/'device-token'
    # First pairing may ask for the normal home SSH password/host-key approval.
    # Secret stdout stays captured in memory, never displayed or put in argv.
    public=key_pair(Path.home()/'.ssh/car-home-tunnel')
    print('Pairing with home over SSH; verify its host fingerprint if prompted. No token will be printed.',flush=True)
    token=home_credentials(args.home,public)
    if credential.exists() and credential.read_text().strip()!=token:
        raise ValueError('Existing Tiny device token differs; refusing silent credential replacement')
    atomic(credential,token.encode())
    # Configuration is installed as data. Privileged execution remains in the
    # existing immutable, verified fleet installer; never sudo this module.
    with tempfile.NamedTemporaryFile(mode='w',prefix='car-radio-link-',delete=False) as temporary:
        pending=Path(temporary.name)
        json.dump(options,temporary);temporary.flush();os.fsync(temporary.fileno())
    try:
        step('Saving Tiny link settings',['sudo','install','-o','root','-g','root','-m','0644',str(pending),str(path)])
    finally:pending.unlink(missing_ok=True)
    step('Applying the verified role settings (may take several minutes)',
         ['sudo','systemctl','start','car-fleet-refresh.service'],timeout=960)
    # The verified reconciler must have adopted the new tunnel support.
    active=step('Checking the persistent home tunnel',['systemctl','is-active','--quiet','car-home-tunnel.service'],check=False)
    if active.returncode:raise ValueError('Home tunnel pending; complete the normal signed Tiny update before pairing again')
    config.update(options)
    agent=Agent(config)
    last=None
    print('Enrolling application encryption keys through the home tunnel…',flush=True)
    for _ in range(6):
        try:
            agent.exchange();last=None;break
        except Exception as exc:
            last=type(exc).__name__;time.sleep(2)
    if last:raise ValueError('Tunnel installed but authenticated home enrollment is pending: '+last)
    step('Starting the automatic encrypted radio test',
         ['sudo','systemctl','restart','car-update-link.service','car-radio-adapter.service'],timeout=60)
    if not options.get('radio_self_test',True) or not options.get('home_tunnel_enabled',True):
        print('Pairing saved; an existing test/tunnel opt-out was preserved. No automatic test was enabled.',flush=True)
        return
    print('Tiny application pairing complete. Encrypted self-test will report automatically; GPS reporting remains deferred.',flush=True)
    # Keep setup nonblocking; the service and result watcher own the long test.
    print('Watch results with: python3 -m control.radio_diagnostic --wait',flush=True)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:
        raise SystemExit('Radio pairing cancelled. Pairing may be incomplete; pending does not mean a radio test ran.')
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        raise SystemExit('Radio pairing: '+str(exc))
