#!/usr/bin/env bash
# Routine update for an already paired Duet/Tiny. No administrator login.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as your normal Duet user, without sudo.' >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TARGET="${1:-cayden@172.16.42.2}"
exec python3 -u "$ROOT/deploy/offline_updates.py" update --target "$TARGET"
