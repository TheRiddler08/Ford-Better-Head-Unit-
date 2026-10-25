#!/usr/bin/env bash
# Long-lived SSH tunnel manager for the Duet touchscreen.
# A Tiny reboot or disconnected cable MUST NOT permanently stop retries.
set -euo pipefail
if (( $# != 2 )); then
  echo "Usage: ssh-tunnel.sh KEY USER@HOST" >&2
  exit 2
fi
KEY="$1"
TARGET="$2"

while :; do
  echo "$(date -Is) Opening wired SSH tunnel to $TARGET"
  # IMPORTANT: ssh must be the condition of an if-statement. If the Tiny is
  # rebooting, ssh normally exits 255; a bare ssh command under set -e would
  # silently terminate this reconnect loop forever.
  if ssh -F /dev/null -N -T \
    -o BatchMode=yes -o StrictHostKeyChecking=yes \
    -o ExitOnForwardFailure=yes -o ConnectTimeout=5 \
    -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
    -o ConnectionAttempts=1 \
    -i "$KEY" \
    -L 127.0.0.1:8788:127.0.0.1:8787 \
    "$TARGET"; then
    echo "$(date -Is) SSH session closed normally; reconnecting."
  else
    status=$?
    echo "$(date -Is) SSH exited with status $status; retrying in 3 seconds."
  fi
  sleep 3
done
