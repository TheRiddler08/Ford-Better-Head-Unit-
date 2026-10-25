#!/usr/bin/env bash
# Pair the car Duet with the Tiny securely over an existing Tailscale connection.
# Run as the Duet graphical user; this never alters the Tiny's networking remotely.
set -euo pipefail
TINY="${1:-}"
if [[ -z "$TINY" ]]; then
  echo "Usage: bash duet/pair-tiny.sh cayden@TINY_TAILSCALE_IP"
  exit 2
fi
[[ "$TINY" =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.:-]+$ ]] || { echo "Expected user@host" >&2; exit 2; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
KEY="$HOME/.ssh/drive-console-duet"
[[ -f "$KEY.pub" && -f "$KEY" ]] || { echo "Run 'bash duet/install.sh' first" >&2; exit 1; }
for c in ssh scp ssh-keyscan ssh-keygen; do
  command -v "$c" >/dev/null || { echo "Missing $c" >&2; exit 1; }
done
# Use regular SSH's host-key verification for the Tailscale transport.
echo "Pairing with $TINY through Tailscale."
ssh "$TINY" 'printf "Connected to Tiny via SSH.\n"'
# Preserve unique key identity. Do not move private key or reveal credentials.
cat "$KEY.pub" | ssh "$TINY" 'umask 077; cat > "$HOME/.car-duet-pair.pub"'
# Helper exists in the repository on Tiny.
ssh "$TINY" 'cd "$HOME/car-dashboard" && bash deploy/authorize-duet-key.sh "$HOME/.car-duet-pair.pub" && rm -f "$HOME/.car-duet-pair.pub"'
echo
echo "Now connect the dedicated Ethernet cable."
echo "On the Tiny, get its Ethernet interface name with: nmcli device status"
echo "For an unattended dedicated Ethernet port, run on Tiny:"
echo "  sudo bash ~/car-dashboard/deploy/setup-ethernet.sh tiny --install --interface IFACE"
echo
echo "For first offline SSH use, verify the Tiny's wired SSH host fingerprint:"
echo "  Tiny: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
echo "  Duet: ssh-keyscan -t ed25519 172.16.42.2 | ssh-keygen -lf -"
echo "Only after they match, on Duet:"
echo "  ssh-keyscan -H -t ed25519 172.16.42.2 >> ~/.ssh/known_hosts"
echo "Do not disable SSH host-key verification."
