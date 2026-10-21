#!/usr/bin/env python3
"""Reversible GDM automatic login configuration."""
import argparse
import os
import pwd
import re
import shutil
import stat
import tempfile
from pathlib import Path

CONFIG = Path("/etc/gdm3/custom.conf")
BEGIN = "# BEGIN CAR-DASHBOARD AUTOLOGIN"
END = "# END CAR-DASHBOARD AUTOLOGIN"
OLD = "# CAR-DASHBOARD-PREVIOUS "
ACTIVE_KEY = re.compile(r"^\s*(AutomaticLoginEnable|AutomaticLogin)\s*=", re.I)
SECTION = re.compile(r"^\[[^\]]+\]\s*$")
USERNAME = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")

def render_disable(source):
    lines = source.splitlines(keepends=True)
    result = []
    inside = False
    for line in lines:
        if line.rstrip("\r\n") == BEGIN:
            inside = True
            continue
        if inside:
            if line.rstrip("\r\n") == END:
                inside = False
            continue
        if line.startswith(OLD):
            line = line[len(OLD):]
        result.append(line)
    if inside:
        raise ValueError("Incomplete car-dashboard GDM block; refusing change")
    return "".join(result)

def render_enable(source, username):
    if not USERNAME.fullmatch(username):
        raise ValueError("Invalid Linux username")
    restored = render_disable(source)
    lines = restored.splitlines(keepends=True)
    index = next((i for i, x in enumerate(lines) if x.strip().lower()=="[daemon]"), None)
    if index is None:
        lines = ["[daemon]\n"] + lines
        index = 0
    elif not lines[index].endswith("\n"):
        lines[index] += "\n"
    end = next((i for i in range(index+1,len(lines)) if SECTION.fullmatch(lines[i].strip())),len(lines))
    for i in range(index+1,end):
        if ACTIVE_KEY.match(lines[i]):
            lines[i] = OLD+lines[i]
    lines[index+1:index+1] = [BEGIN+"\n","AutomaticLoginEnable=true\n",
                            "AutomaticLogin="+username+"\n",END+"\n"]
    return "".join(lines)

def atomic_write(path, content):
    old = path.stat()
    fd, temp = tempfile.mkstemp(prefix=".car-gdm-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
            os.fchmod(f.fileno(),stat.S_IMODE(old.st_mode))
            os.fchown(f.fileno(),old.st_uid,old.st_gid)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["enable","disable"])
    parser.add_argument("username",nargs="?")
    args=parser.parse_args()
    if os.geteuid()!=0:
        parser.error("Run as root with sudo")
    if not CONFIG.exists():
        parser.error("GDM not found at /etc/gdm3/custom.conf")
    if args.action=="enable":
        if not args.username:
            parser.error("Username required")
        pwd.getpwnam(args.username)
    before=CONFIG.read_text(encoding="utf-8")
    after=render_enable(before,args.username) if args.action=="enable" else render_disable(before)
    if before!=after:
        backup_dir=Path("/etc/car-dashboard")
        backup_dir.mkdir(mode=0o755,parents=True,exist_ok=True)
        backup=backup_dir/"gdm3.before-kiosk.conf"
        if not backup.exists():
            shutil.copy2(CONFIG,backup)
            backup.chmod(0o600)
        atomic_write(CONFIG,after)
    print("GDM "+("automatic login enabled" if args.action=="enable" else "previous login behavior restored")+"; reboot to apply.")

if __name__=="__main__":
    main()
