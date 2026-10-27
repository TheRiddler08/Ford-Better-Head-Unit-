#!/usr/bin/env bash
# One-time Tiny setup. May also run from a copied bootstrap directory.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as the Tiny dashboard user, not root.' >&2; exit 1; }
PUB="${1:?Usage: bash install-courier.sh /path/to/duet-courier.pub [repository]}"
REPO="$(realpath "${2:-$HOME/car-dashboard}")"
USER_NAME="$(id -un)"
[[ "$REPO" =~ ^/[a-zA-Z0-9_./-]+$ && "$USER_NAME" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'Unsupported account/repository path' >&2; exit 1; }
[[ "$(git -C "$REPO" rev-parse --show-toplevel)" == "$REPO" ]] || { echo 'Expected the dashboard checkout root.' >&2; exit 1; }
SOURCE="$(cd "$(dirname "$0")" && pwd -P)/offline_updates.py"
[[ -f "$SOURCE" ]] || { echo 'Keep offline_updates.py beside this installer.' >&2; exit 1; }
ssh-keygen -lf "$PUB" >/dev/null
read -r TYPE KEY _ < "$PUB"
[[ "$TYPE" == ssh-ed25519 && "$KEY" =~ ^[A-Za-z0-9+/=]+$ ]] || { echo 'Expected Ed25519 courier public key' >&2; exit 1; }
if [[ -f "$HOME/.ssh/authorized_keys" ]] && grep -Fq " $KEY" "$HOME/.ssh/authorized_keys"; then
 EXISTING="$(grep -F " $KEY" "$HOME/.ssh/authorized_keys")"
 [[ "$EXISTING" == *'restrict,command="sudo -n /usr/local/sbin/car-courier-receive" '* ]] || { echo 'Key already authorized for another purpose. Use a separate courier key.' >&2; exit 1; }
fi
echo 'Authorizing this separate key to deploy signed dashboard updates. Display key remains unchanged.'
sudo install -d -m 0755 /usr/local/lib/car-dashboard /etc/car-dashboard
sudo install -o root -g root -m 0644 "$SOURCE" /usr/local/lib/car-dashboard/offline_updates.py
printf 'duet %s %s\n' "$TYPE" "$KEY" | sudo tee /etc/car-dashboard/courier.allowed_signers >/dev/null
sudo chmod 0644 /etc/car-dashboard/courier.allowed_signers
sudo tee /usr/local/sbin/car-courier-receive >/dev/null <<WRAPPER
#!/bin/sh
set -eu
[ "\$#" -eq 0 ] || exit 2
exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/python3 -I /usr/local/lib/car-dashboard/offline_updates.py receive --repo '$REPO' --user '$USER_NAME' --allowed-signers /etc/car-dashboard/courier.allowed_signers
WRAPPER
sudo chown root:root /usr/local/sbin/car-courier-receive
sudo chmod 0755 /usr/local/sbin/car-courier-receive
TEMP="$(mktemp)"
trap 'rm -f "$TEMP"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/car-courier-receive ""\n' "$USER_NAME" > "$TEMP"
sudo visudo -cf "$TEMP"
sudo install -o root -g root -m 0440 "$TEMP" /etc/sudoers.d/car-courier
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
if ! grep -Fq " $KEY" "$HOME/.ssh/authorized_keys"; then
 printf 'restrict,command="sudo -n /usr/local/sbin/car-courier-receive" %s %s car-update-courier\n' "$TYPE" "$KEY" >> "$HOME/.ssh/authorized_keys"
else
 echo 'Key already present in authorized_keys; retaining its existing restrictions. Check it is the courier key, not a shell/display key.'
fi
echo 'Tiny receiver installed. Pin the Tiny host key on Duet before first delivery.'
echo 'No networking, recorder, database, or dashboard source was changed by this bootstrap.'
