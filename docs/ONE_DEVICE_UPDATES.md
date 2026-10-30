# One Internet-connected device: the Duet

Only the **Duet** needs Wi-Fi/Internet. It downloads Git source, the official
Home Assistant image for the **amd64 Tiny**, and Ubuntu's signed Docker dependency
packages. Later, over the wired USB/Ethernet SSH connection, the Tiny verifies and
installs the carried update without contacting a registry or package mirror.

This updates an existing car installation. The Tiny must already have Ubuntu,
Python 3, Git, SSH, sudo, a dashboard checkout on `main`, and its working
`car-dashboard.service`. It does not install an operating system or guess which
serial adapter is the GPS versus OBD device.

## One command while Tiny is disconnected

On Duet's Wi-Fi, as your normal desktop user:

```sh
cd ~/car-dashboard
git pull --ff-only
bash duet/prepare-update.sh cayden@172.16.42.2
```

This installs the current Duet display/courier, starts downloads, and waits for
Tiny to reconnect locally. It then runs the one-time receiver bootstrap, including
upgrading older v1 receivers, without requiring Internet or a Git pull on Tiny.
Normal admin SSH/sudo authorization and host-key verification still apply; an
unpaired Tiny can ask for its normal password once. The display-only key cannot
authorize software installation. Tiny still needs its original working dashboard,
SSH and sudo; this does not provision a blank computer.

Leave the terminal open. If Duet reboots, an interactive pairing terminal resumes
at the next **desktop login** until pairing succeeds. You can also resume offline
with `bash ~/.local/share/drive-console/finish-courier-pairing.sh`. Downloads use
an independent worker. Keep Duet online until a full bundle is ready before
carrying it away; successful pairing alone does not mean downloading is complete.
After pairing, future delivery uses the normal automatic courier. Keep Tiny
powered until installation finishes.

## One-time setup, all initiated on Duet

1. At home, connect **Duet** to Wi-Fi and update its checkout:

   ```sh
   cd ~/car-dashboard
   git pull --ff-only
   bash duet/install-courier.sh cayden@172.16.42.2
   ```

   This starts downloads even while Tiny is elsewhere. If the first bundle is not
   complete yet, leave Duet on Wi-Fi until status says ready.

2. Connect Duet to Tiny using the existing wired connection. The Tiny does **not**
   need Wi-Fi. As your normal Duet user, run:

   ```sh
   bash duet/setup-offline-updates.sh cayden@172.16.42.2
   ```

   Replace `cayden` with the Tiny's ordinary dashboard account if different. This
   uses your normal administrative SSH login once and asks for the normal sudo
   authorization on each machine. Verify the Tiny host-key fingerprint using your
   trusted existing connection before accepting it. Do not disable host checking.
   Your display-only SSH key is not sufficient for this one-time admin operation.

3. The setup creates a separate courier signing/SSH key on Duet, installs the
   restricted root receiver on Tiny, and starts Duet's automatic worker. It
   selects a system boot service for systemd or OpenRC; there is no systemd
   **user** bus requirement. Other init systems use graphical autostart and start
   the worker immediately; those need a desktop login after reboot.

4. Leave Duet on Wi-Fi for the first large download. If Wi-Fi is only available
   inside your house, disconnect it from Tiny and bring it inside: the download
   can finish there. Look for **Car updates - status** in its application menu.
   `prepared.json` identifies a completed, signed ready bundle; `activity.json`
   distinguishes downloading, ready, and retrying states.

5. Reconnect Duet to Tiny. Installation starts automatically when a ready package
   is present. Tiny's **Updates → Duet update courier** shows receiving, verifying,
   installing, failed or completed status. The previous dashboard stays available
   while a package is downloaded or a transfer is incomplete.

If you have not yet installed the latest Duet display launcher/proxy, run
`bash duet/install.sh` on Duet and restart its graphical session once. Subsequent
managed display-helper updates are copied automatically; running native helpers
activate at the next natural display restart. Application/dashboard pages served
by Tiny update through their existing reload/reconnect mechanisms.

Existing v1 courier installations need this one-time bootstrap upgrade: v1
receivers intentionally reject the new multi-artifact format. Do not merely copy
an SSH private key or use the display key for software installation.

## Normal routine — no update commands

- Duet checks Git, Home Assistant **stable**, and official Ubuntu metadata hourly.
  Failed downloads retry after two minutes; partial downloads never replace a
  completed ready package. Content-addressed caches avoid fetching unchanged
  layers/packages repeatedly.
- A separate transfer worker tries Tiny every 30 seconds. A slow/offline Internet
  request cannot block delivery of an already-cached bundle.
- A changed HA image or dependency set is a new delivery even when the Git commit
  has not changed. Completed receipts include the full payload identity.
- Install/validation failures keep the update queued with increasing retry delays
  rather than restarting HA every 30 seconds. A newly downloaded payload gets a
  fresh attempt. Connectivity failures retry quickly.
- Managed Duet worker code updates itself only after an active transfer finishes.
  The Tiny receiver publishes a complete, versioned bootstrap from the signed
  Git commit and atomically switches its selector after successful validation.
- If the Tiny was deliberately configured with HA disabled or stopped, the
  courier preserves that choice. It does not silently turn the service back on.

The first bundle is large (typically around 1–2 GB, depending on packages). Allow
roughly **12 GB free on each device**, preferably more, for caches, temporary
staging, Docker image extraction and HA configuration backups. Cached artifacts
and the current ready bundle are retained for retry. Do not expect a release that
was never downloaded to appear on an offline device.

## What gets installed offline

The default full bundle includes:

- the complete Git bundle for this repository's `main` branch;
- the official Home Assistant stable image, resolved on Duet and locked to exact
  manifest/config/layer hashes for that bundle;
- the dependency closure for Docker Engine, CA certificates, GPG verification and
  Ubuntu archive keys, with signed repository metadata for Ubuntu **22.04, 24.04
  and 26.04 amd64**. Tiny chooses its own release; ARM packages are never installed
  merely because Duet is ARM.

The Duet downloader is Python plus its normal system zstd library. It does not
need Docker, QEMU or an Ubuntu userspace. The installer adds the zstd library on
Duet if needed. Ubuntu dependencies are installed only on Tiny using native APT
with its own trusted archive keys, a temporary **local-file-only** repository,
and `--no-download`. Persistent apt sources and signature checks are not relaxed.

First carried installation can install Docker and configure Home Assistant on
Tiny entirely offline. Home Assistant's normal owner-account screen still needs
completion once, if you have not already created an owner. Existing accounts and
native dashboard storage are retained. Account setup does not require another
Internet-connected car component.

HA image updates stop the old instance, back up the complete private configuration
and database, validate the candidate, and check both HTTP and the live Car Console
integration/11 entities before accepting it. Failed upgrades restore the previous
container and HA configuration. The latest failed recovery backups are retained;
older failed backups for that same candidate are bounded. Successful upgrade
backups remain available for recovery. Monitor storage.

The original vehicle/GPS SQLite archives are never bundled or pruned. Native HA's
own short-term history is separate. OS dependency installation is a normal package
manager operation, not a complete OS snapshot: it is not automatically downgraded
if a later dashboard/HA check fails.

## Previous features remain

| Feature | Where it remains available |
| --- | --- |
| Live car gauges and GPS position | Home Assistant native entities/dashboard |
| Detailed values, every recorded OBD request, scans, errors, CSV | Original **Reports & GPS history** page |
| Full GPS paths and visited stops | Original local route archive |
| HTML/CSS/JavaScript visual builder and versioned custom layouts | Existing home control panel |
| Original car dashboard | Standard-dashboard/recovery links |
| Recorders running without Duet | Existing Tiny OBD/GPS services |

Those features have not all been rewritten as HA cards. Existing custom HTML
projects are not automatically converted to Lovelace layouts. This courier carries
software/runtime updates; it does **not** automatically synchronize home-panel
drafts, arbitrary files, firmware, every Linux package, or a second Home Assistant
instance. Existing home configuration delivery still uses its configured Tailscale
connection. GPS receiver configuration still needs the correct device selected.

## Logs and manual status

On Duet:

```sh
python3 ~/.local/share/drive-console/courier-current/offline_updates.py status
```

For a systemd install:

```sh
sudo journalctl -u car-duet-courier -n 60 --no-pager
```

For OpenRC or graphical-autostart fallback:

```sh
tail -n 60 ~/.local/state/drive-console/courier.log
```

On Tiny:

```sh
curl -fsS http://127.0.0.1:8787/api/update-status
sudo journalctl -u car-home-assistant -n 60 --no-pager
```

A receipt confirms the requested payload finished validation, not merely that a
file was copied. A failed or deferred payload is not marked installed. Expired or
unverifiable Ubuntu metadata, unsupported Tiny OS, local source edits, full disks,
missing privileges and bad host keys remain explicit failures; refresh/retry or
resolve the stated cause rather than bypassing verification.

`--code-only` is an explicit legacy mode for Git-only transfers. It does not supply
or install HA/Docker dependencies and is not the default automatic setup.

## Trust and one-time authorization

The separate courier key authorizes **privileged installer and managed bootstrap
updates**, not only dashboard files. Protect the Duet user account/private key and
repository write access accordingly. The private key stays on Duet. The Tiny key
is restricted to the fixed receiver (no shell/PTY/general forwarding); the signed
metadata and Git objects determine what is accepted. Bootstrap source is taken
from the verified immutable commit, never an editable working-tree file.

Supported native package installation was exercised in a real Ubuntu 24.04
container after disconnecting its network. The rootless image downloader's output
was loaded by Docker, with all layer and image identities checked. Other Ubuntu
bundles are generated from their official signed metadata; physical Tiny/OpenRC/
USB/two-machine operations still need validation on your devices.

## Ignition-switched power

Keep Tiny powered throughout update installation. See [power-loss protection](POWER_LOSS.md) for recorder durability, the disk-flush timer and the retained-power hardware needed for reliable shutdown. Being parked does not guarantee power remains available.
