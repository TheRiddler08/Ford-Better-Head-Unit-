# Carry software updates home and back on the Duet

No LoRaWAN hardware is required. The Duet downloads repository software on Wi-Fi,
retains a complete signed Git bundle, and transfers it to the Tiny over the existing
USB/Ethernet SSH connection. The Tiny needs no Internet for this transfer. The
Duet remains a display; do not install `car-dashboard.service` on it.

This carries repository **software updates**, not Linux packages, firmware,
location databases or home panel drafts. Home-published layouts/settings still
use their configured Tailscale synchronization path. Newly required OS/Python
packages must be installed separately once; the changes shipped with this courier
use the existing Python standard library. gpsd is a separate one-time prerequisite
for GPS recording.

## 1. On the Duet at home (Wi-Fi)

Run as its normal desktop user:

```sh
cd ~/car-dashboard
git pull --ff-only
bash duet/install.sh
bash duet/install-courier.sh cayden@172.16.42.2
python3 deploy/offline_updates.py prepare
```

Replace `cayden` if the Tiny's dashboard account has another name. This creates a
**separate** Ed25519 courier key at `~/.ssh/drive-console-courier` and downloads a
ready archive under `~/.local/share/drive-console/courier/ready.zip`. Never copy the
private key off the Duet. Its `.pub` file is safe to give the Tiny. The existing
`drive-console-duet` display key remains restricted to dashboard port forwarding.

Restart the Duet kiosk or log out and back in once after this first install so
its updated display proxy supports streaming large CSV exports. No dashboard
systemd unit is needed on the Duet.

The script adds desktop launcher entries for prepare, deliver and status and
an optional courier worker in graphical autostart. It does not need a systemd
user bus, so it also works with the terminal setup that lacked `XDG_RUNTIME_DIR`.

## 2. One-time setup on the Tiny

Do this while parked. The Tiny must already have its normal dashboard service,
Git, Python 3, OpenSSH server and sudo installed. For signing verification it needs
OpenSSH with `ssh-keygen -Y` support (Ubuntu 22.04+ supplies it).

Verify the Tiny's host-key fingerprint using a trusted existing connection or
its local terminal:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On the Duet compare the result of:

```sh
ssh-keyscan -t ed25519 172.16.42.2 | ssh-keygen -lf -
```

Only when they match, pin the host key if not already pinned:

```sh
ssh-keyscan -H -t ed25519 172.16.42.2 >> ~/.ssh/known_hosts
```

Do not blindly accept a changed host key or disable strict checking.

You can bootstrap the Tiny while it is **already offline**, using the Duet's
normal administrative SSH login (not its display-only or courier key):

```sh
ssh cayden@172.16.42.2 'mkdir -p "$HOME/car-courier-bootstrap"'
scp deploy/offline_updates.py deploy/install-courier.sh \
  ~/.ssh/drive-console-courier.pub \
  cayden@172.16.42.2:car-courier-bootstrap/
ssh -t cayden@172.16.42.2 \
  'bash "$HOME/car-courier-bootstrap/install-courier.sh" "$HOME/car-courier-bootstrap/drive-console-courier.pub" "$HOME/car-dashboard"'
```

Use the normal Tiny account/password or an existing admin key. The installer asks
for sudo through that terminal. If the host is not already pinned, establish the
verified regular SSH connection first; the courier itself always requires a known
host key. Keep the bootstrap files outside the checkout so they don't appear as
untracked source changes.

The installer pins the public signing identity, installs a root-owned standalone
receiver, and authorizes the separate SSH key only for that receiver. It gives no
shell, PTY, agent forwarding or general port-forwarding permission. The receiver's
sudo rule accepts no arguments. It does not alter the display key or networking.

## 3. First delivery, from Duet

```sh
cd ~/car-dashboard
python3 deploy/offline_updates.py deliver --target cayden@172.16.42.2
python3 deploy/offline_updates.py status
```

Success reports `"ok": true` and the exact installed commit. The Tiny dashboard's
**Updates → Duet update courier** section shows its receipt. The normal browser
build watcher reloads when the Tiny's Git revision changes.

The receiver verifies the signature, expected repository/branch, SHA-256 checksum,
archive entries and Git objects before updating. It requires a clean checkout on
`main`, refuses rewrites/downgrades and ignored-file collisions, runs the standard
unit suite as the ordinary dashboard user, restarts the dashboard and checks its
HTTP/database health. Adopted, currently active recorder services are restarted
only if their source changed. Intentionally stopped recorders remain stopped.
Database/history files and local configuration are not bundled or deleted.

Failed validation rolls back the checkout and restores the previously running
services. Rollback refuses to overwrite concurrent user edits; if such edits
prevent rollback, resolve them locally rather than forcing a reset. The receiver
shares a lock with the normal GitHub updater. Interrupted transfers, offline Tiny,
invalid signatures, local edits and failed health checks do not produce a success
receipt. A lost final response can safely be retried; the ready archive is retained.
The receiver refuses installation if the dashboard service is stopped or missing.

## 4. Normal use after pairing

The courier worker starts at the next graphical login. To start it immediately:

```sh
nohup ~/.local/share/drive-console/courier-watch.sh >/dev/null 2>&1 &
```

It checks Wi-Fi downloads hourly (retries failed downloads after two minutes),
keeps the last complete package if offline, and attempts delivery every 30 seconds
while the Tiny is reachable. A process lock prevents duplicate workers. It skips
commits already acknowledged for that target. If you reinstall/restore the Tiny,
remove the Duet's stale `~/.local/share/drive-console/courier/delivered.json` receipt
before retrying the same commit.

The download step also refreshes already-installed Duet display helpers when their
contents match a known repository version. Local customizations and missing
installations are preserved. Changed native helpers take effect at the next
kiosk/display restart; the current browser is not forcibly closed. The dashboard
pages themselves come from the Tiny and reload after its update.

Your routine is now:

1. Bring the Duet home and connect it to Wi-Fi. Give the worker time to download,
   or click **Car updates - download** to download immediately.
2. Check **Car updates - status** for the prepared commit.
3. Reconnect it to the Tiny in the parked car. Delivery runs automatically.
4. Check the Tiny's Updates page for the receipt. No radio or Tiny Wi-Fi is needed.

For logs:

```sh
tail -n 50 ~/.local/state/drive-console/courier.log
```

The installed worker/receiver are stable bootstrap copies. A later source update
does not silently replace the privileged receiver. To upgrade that component,
rerun the relevant installer with administrative access. To remove automatic
carrying, remove `~/.config/autostart/car-update-courier.desktop` on the Duet and
stop its courier worker. To revoke the receiver, remove its specific authorized
key line and `/etc/sudoers.d/car-courier` on the Tiny. Do not delete other SSH keys.

The key authorizes source-code deployment: protect the Duet account/private key
and repository write access. This is not a package manager for arbitrary programs
or an unattended ECU updater. Actual two-machine SSH/systemd operation and recovery
still require checking on your hardware; automated tests use isolated local Git
repositories and signing keys.
