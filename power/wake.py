#!/usr/bin/env python3
"""Send a configurable WOL burst; successful transmission is not a wake receipt."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import socket
import time


def packet(mac):
    if not isinstance(mac,str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac):
        raise ValueError('Use an Ethernet MAC such as 02:11:22:33:44:55')
    raw=bytes.fromhex(mac.replace(':',''))
    if raw==b'\0'*6 or raw[0]&1:raise ValueError('Target MAC must be an individual device address')
    return b'\xff'*6+raw*16


def targets(config):
    if config.get('schema')!=1:raise ValueError('Unsupported wake configuration')
    rows=config.get('targets')
    if not isinstance(rows,list) or not 1<=len(rows)<=16:raise ValueError('Configure 1–16 wake targets')
    for row in rows:
        packet(row['mac'])
        ipaddress.IPv4Address(row['broadcast'])
        if type(row.get('port',9)) is not int or not 1<=row.get('port',9)<=65535:raise ValueError('Invalid UDP port')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}',row['name']):raise ValueError('Invalid target name')
        if row.get('interface') and not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}',row['interface']):raise ValueError('Invalid Pi interface')
    return rows


def send(config):
    failed=[]
    for row in targets(config):
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
                sock.settimeout(3)
                if row.get('interface'):
                    sock.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,row['interface'].encode()+b'\0')
                for _ in range(3):
                    sock.sendto(packet(row['mac']),(row['broadcast'],row.get('port',9)))
                    time.sleep(.2)
            print(row['name']+': magic-packet burst sent (wake not confirmed)',flush=True)
        except OSError as exc:
            failed.append(row['name']);print(row['name']+': send failed: '+str(exc),flush=True)
    if failed:raise RuntimeError('Failed targets: '+', '.join(failed))


def status_settings(config):
    status=config['status']
    ip=ipaddress.IPv4Address(status['bind'])
    if ip.is_unspecified or ip.is_multicast:raise ValueError('Bind status to the actual Pi Ethernet IPv4 address')
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}',status['interface']):raise ValueError('Invalid status Ethernet interface')
    return status


def serve(config):
    status=status_settings(config)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,status['interface'].encode()+b'\0')
        sock.bind((status['bind'],8799))
        print('Pi wake timing responder ready on its Ethernet LAN',flush=True)
        while True:
            data,peer=sock.recvfrom(256)
            if data!=b'CAR_WAKE_STATUS_V1':continue
            seconds=float(Path('/proc/uptime').read_text().split()[0])
            response=json.dumps({'schema':1,'uptime':seconds,'lastWake':150}).encode()
            sock.sendto(response,peer)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/etc/car-dashboard/pi-wake.json')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true')
    mode.add_argument('--serve',action='store_true')
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    if args.check:
        targets(config);status_settings(config);print('Wake configuration valid; no packets sent')
    elif args.serve:serve(config)
    else:send(config)
