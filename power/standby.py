#!/usr/bin/env python3
"""Opt-in, once-per-boot suspend; firmware/NIC wake must be physically tested."""
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
    if config.get('schema')!=1 or config.get('role') not in ('duet','tiny'):raise ValueError('Invalid standby role/schema')
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
    if config['role']=='tiny':return [Path('/run/lock/car-dashboard-update.lock')]
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


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check','boot','test-suspend'])
    parser.add_argument('--config',default='/etc/car-dashboard/boot-standby.json')
    args=parser.parse_args()
    if args.action=='boot':
        while not Path(args.config).exists() and uptime()<120:time.sleep(1)
        if not Path(args.config).exists():raise SystemExit('Role configuration is not ready; staying awake')
    config=json.loads(Path(args.config).read_text())
    if args.action=='check':
        probe(config);print('Reported capabilities pass. Physical wake test still required.')
    else:
        if os.geteuid()!=0:raise SystemExit('Suspend preparation must run as root')
        sleep_once(config,test=args.action=='test-suspend')
