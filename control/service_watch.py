"""Run a home service as its normal user, reloading a private environment file."""
import argparse
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time


def environment(path):
    result={key:value for key,value in os.environ.items() if not key.startswith('CAR_')}
    for line in Path(path).read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        key,sep,value=line.partition('=')
        if not sep or not key.startswith('CAR_') or not key.replace('_','').isalnum():raise ValueError('Invalid home environment entry')
        words=shlex.split(value,comments=False,posix=True)
        if len(words)>1:raise ValueError('Quote values containing spaces')
        result[key]=words[0] if words else ''
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--env',required=True);parser.add_argument('--module',required=True);args=parser.parse_args()
    if args.module not in ('control.home_server','control.chirpstack_bridge','control.home_ha','control.file_sync'):raise ValueError('Unknown home service')
    child=None
    def stop(*_):raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        while True:
            stamp=Path(args.env).stat().st_mtime_ns
            child=subprocess.Popen([sys.executable,'-m',args.module]+(['--home'] if args.module=='control.file_sync' else []),env=environment(args.env))
            while child.poll() is None and Path(args.env).stat().st_mtime_ns==stamp:time.sleep(1)
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            else:time.sleep(10)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()


if __name__=='__main__':main()
