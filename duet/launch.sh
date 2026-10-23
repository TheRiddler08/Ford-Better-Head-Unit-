#!/usr/bin/env bash
# Run only in the tablet's graphical session (XDG autostart or terminal).
# Tablet: postmarketOS USB gadget at 172.16.42.1
# Tiny:   Ubuntu USB Ethernet at 172.16.42.2
set -euo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TINY_HOST="${DRIVE_TINY_HOST:-172.16.42.2}"
TINY_USER="${DRIVE_TINY_USER:-cayden}"
KEY="$HOME/.ssh/drive-console-duet"
URL="http://127.0.0.1:8877/"
if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "Start the Duet kiosk from its graphical desktop, not SSH." >&2
  exit 1
fi
[[ -f "$KEY" ]] || { echo "Run duet/install.sh to generate the SSH key." >&2; exit 1; }
mkdir -p "$HOME/.local/state/drive-console"
LOG="$HOME/.local/state/drive-console/kiosk.log"
exec >>"$LOG" 2>&1
echo "$(date -Is) Starting Duet display."
python3 -u "$BASE/proxy.py" --port 8877 --upstream-port 8788 &
PROXY_PID=$!
(
  while :; do
    echo "$(date -Is) Opening USB SSH tunnel to $TINY_HOST"
    ssh -F /dev/null -N -T \
      -o BatchMode=yes -o StrictHostKeyChecking=yes \
      -o ExitOnForwardFailure=yes -o ConnectTimeout=5 \
      -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
      -o ConnectionAttempts=1 \
      -i "$KEY" \
      -L 127.0.0.1:8788:127.0.0.1:8787 \
      "$TINY_USER@$TINY_HOST"
    echo "$(date -Is) USB tunnel disconnected; reconnecting."
    sleep 3
  done
) &
SSH_RECONNECT_PID=$!
cleanup(){
  kill "$SSH_RECONNECT_PID" "$PROXY_PID" 2>/dev/null || true
  wait "$SSH_RECONNECT_PID" "$PROXY_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
sleep 2
while :; do
  if command -v firefox >/dev/null 2>&1; then
    firefox --kiosk --private-window "$URL" || true
  elif command -v chromium >/dev/null 2>&1; then
    chromium --kiosk --incognito --no-first-run \
      --user-data-dir="$HOME/.local/state/drive-console/chromium" "$URL" || true
  else
    echo "Browser missing; install Firefox or Chromium." >&2
    exit 1
  fi
  sleep 5
done
