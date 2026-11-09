#!/usr/bin/env bash
# One-time explicit enrollment. Routine credential sharing has no prompts.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the normal Duet user.' >&2; exit 1; }
TARGET="${1:-cayden@172.16.42.3}"
[[ "$TARGET" =~ ^([a-zA-Z0-9_-]+)@(172\.16\.42\.[234])$ ]] || { echo 'Use car user@172.16.42.3 (Pi), .2 (Tiny) or .4 (laptop).' >&2; exit 2; }
PEER_USER="${BASH_REMATCH[1]}"; PEER_ADDRESS="${BASH_REMATCH[2]}"
case "$PEER_ADDRESS" in 172.16.42.3) ROLE=pi;; 172.16.42.2) ROLE=tiny;; 172.16.42.4) ROLE=laptop;; esac
HELPER=/usr/local/lib/car-dashboard/fleet-current/wifi_profiles.py
[[ -f "$HELPER" ]] || { echo 'First update the Duet and run: bash deploy/install-fleet.sh duet' >&2; exit 1; }
# Execute the root-owned installed helper, not editable checkout code.
exec sudo /usr/bin/python3 -I "$HELPER" pair --role "$ROLE" --user "$PEER_USER"
