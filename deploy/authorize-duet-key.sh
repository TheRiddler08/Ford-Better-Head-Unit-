#!/usr/bin/env bash
# Run on the Tiny as the dashboard's Ubuntu user.
# Authorize a SINGLE Duet display-only SSH key for dashboard 8787 and Home Assistant 8123.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as your normal Ubuntu user' >&2; exit 1; }
[[ -f "${1:-}" ]] || { echo 'Usage: bash deploy/authorize-duet-key.sh /path/to/duet.pub' >&2; exit 2; }
command -v ssh-keygen >/dev/null || { echo 'OpenSSH tools required' >&2; exit 1; }
read -r TYPE KEY COMMENT < "$1"
[[ "$TYPE" == "ssh-ed25519" && "$KEY" =~ ^[A-Za-z0-9+/=]+$ ]] ||
  { echo 'Invalid Ed25519 SSH public key' >&2; exit 1; }
ssh-keygen -lf "$1" >/dev/null || { echo 'Public key does not validate' >&2; exit 1; }
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
if [[ -f /etc/car-dashboard/home-assistant-settings.json ]]; then
 python3 - "$HOME/.ssh/authorized_keys" <<'PYHELPER'
import sys
from pathlib import Path
path=Path(sys.argv[1]);content=path.read_text()
lines=[]
for line in content.splitlines(keepends=True):
 if line.rstrip().endswith(' drive-console-duet') and 'command="/bin/false",restrict,port-forwarding,permitopen="127.0.0.1:8787"' in line and 'permitopen="127.0.0.1:8123"' not in line:
  line=line.replace('permitopen="127.0.0.1:8787"','permitopen="127.0.0.1:8787",permitopen="127.0.0.1:8123"',1)
 lines.append(line)
path.write_text(''.join(lines))
PYHELPER
fi
if grep -Fq " $KEY" "$HOME/.ssh/authorized_keys"; then
  echo 'Duet public key already authorized; no changes needed.'
  exit 0
fi
EXTRA=''
if [[ -f /etc/car-dashboard/home-assistant-settings.json ]]; then EXTRA=',permitopen="127.0.0.1:8123"'; fi
printf 'command="/bin/false",restrict,port-forwarding,permitopen="127.0.0.1:8787"%s %s %s drive-console-duet\n' \
  "$EXTRA" "$TYPE" "$KEY" >> "$HOME/.ssh/authorized_keys"
echo 'Duet display key authorized for local dashboard forwarding.'
echo 'SSH on Tiny must be enabled; dashboard stays localhost-only.'
