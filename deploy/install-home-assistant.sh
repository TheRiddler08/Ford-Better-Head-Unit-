#!/usr/bin/env bash
# Tiny only; preserves Ubuntu, recorders, SSH networking and the old dashboard.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the normal Tiny dashboard user; sudo will be requested.' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TIMEZONE="${CAR_HA_TIMEZONE:-$(cat /etc/timezone 2>/dev/null || printf UTC)}"
sudo /usr/bin/python3 "$ROOT/deploy/home_assistant.py" install --repo "$ROOT" --user "$(id -un)" --timezone "$TIMEZONE"
