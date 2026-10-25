# Offline Duet ↔ Tiny Ethernet, with Tailscale for setup and management

The **Duet 3 running Nura/postmarketOS** renders the fullscreen dashboard.
The **Ubuntu ThinkCentre Tiny** runs the dashboard and OBD/GPS services.
The direct Ethernet cable is the normal transport. **No Wi-Fi, internet,
Tailscale, or router is needed once the connection has been paired.**

## Physical connection

Connect a USB-C Gigabit Ethernet adapter to the Duet and its RJ45 cable to a
**dedicated, unused Ethernet interface** on the Tiny. Keep the Duet's separate
USB-C PD charger connected. If the Tiny's built-in Ethernet is already used
for internet or management, add a separate USB Ethernet adapter to the Tiny.

Expected private link:

| Device | Address |
| --- | --- |
| Duet Ethernet | `172.16.42.1/24` |
| Tiny Ethernet | `172.16.42.2/24` |

No DHCP server, DNS, or default route is configured on this link. Other
NetworkManager and Tailscale connections remain as-is.

## Install the automatic networking profiles

Ensure NetworkManager is installed and controlling the dedicated adapters.
Install `iproute2` on the Duet (BusyBox `ip` lacks required flags).

Duet (normal desktop user):

```sh
sudo apk add bash iproute2 networkmanager openssh-client python3 firefox git
cd ~/car-dashboard
git pull --ff-only
bash duet/install.sh
nmcli device status
sudo bash deploy/setup-ethernet.sh duet --install
```

The Duet installer only auto-selects a **free USB Ethernet adapter**. The
timer checks every 30 seconds for an adapter, so it can be connected after
installation. If multiple adapters are free, supply the intended interface
explicitly:

```sh
sudo bash deploy/setup-ethernet.sh duet --install --interface enxYOUR_ADAPTER
```

Tiny (normal Ubuntu user):

```sh
sudo apt-get install network-manager iproute2 openssh-server
sudo systemctl enable --now ssh
cd ~/car-dashboard
git pull --ff-only
nmcli device status
sudo bash deploy/setup-ethernet.sh tiny --install --interface YOUR_DEDICATED_ETHERNET
```

**Important:** Always specify the Tiny's dedicated interface. The script
refuses an interface with an active NetworkManager connection, globally
assigned IP address, or default route. It does not create a gateway or change
Tailscale. It will not overwrite a profile of the same name. If it cannot
identify exactly one safe interface, it does nothing.

Check timers and addresses:

```sh
systemctl list-timers 'car-duet-ethernet*'
nmcli connection show
ip -4 addr show
```

For a different physical adapter, remove the old, *project-created*
`car-duet-wired-*` profile using NetworkManager only after verifying the
correct interface; then rerun the installer with `--interface`. Do not delete
or edit other connection profiles.

## Pair using Tailscale (one-time, internet available)

Tailscale is optional for **pairing and remote administration**, not a
dependency of the offline dashboard. Install it and join both computers to
the same tailnet independently before pairing. On the Tiny, record the Tailscale
IP with `tailscale ip -4`.

On the **Duet**:

```sh
cd ~/car-dashboard
bash duet/install.sh
bash duet/pair-tiny.sh cayden@100.x.y.z
```

The pairing tool uses verified SSH host keys over the Tailscale address,
copies only the Duet's **public** SSH key, and runs
`deploy/authorize-duet-key.sh` on the Tiny. If SSH asks about an unknown
host key, verify its fingerprint on the Tiny before accepting it.
The Tiny repository must already exist at `~/car-dashboard`.
No private keys or administrator passwords are copied. The pairing helper
**does not alter the Tiny's network**; the dedicated interface is configured
with the explicit installer command above.

For offline wired access, on the Tiny run:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On the Duet:

```sh
ssh-keyscan -t ed25519 172.16.42.2 | ssh-keygen -lf -
```

Verify that the two fingerprints match before running this on the Duet:

```sh
ssh-keyscan -H -t ed25519 172.16.42.2 >> ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
```

The dashboard stays bound to the Tiny's `127.0.0.1:8787`; the tunnel
forwards to the Duet at `172.16.42.2`. No publicly accessible dashboard
port is needed. **Never use `StrictHostKeyChecking=no`**.

## Boot and test, entirely offline

Once both wired interfaces show their addresses:

```sh
# On Duet:
ping -c 2 172.16.42.2
# On Tiny:
ping -c 2 172.16.42.1
curl -fsS http://127.0.0.1:8787/api/health
```

In the logged-in Duet GNOME session, launch:

```sh
~/.local/share/drive-console/launch.sh
```

Visit `http://127.0.0.1:8877/` on the Duet, or inspect:

```sh
curl -i http://127.0.0.1:8877/__link
tail -n 60 ~/.local/state/drive-console/kiosk.log
```

The dashboard launcher already reconnects automatically after a cable
disconnect or Tiny reboot. It autostarts after a **GNOME graphical login**,
not necessarily before login. For truly unattended operation, separately
configure and test GNOME auto-login and power management; do not disable a
known-good display or the Tiny's fallback kiosk until the link works.

For a real offline test, turn off Wi-Fi and disable Tailscale temporarily,
then reboot the pair and confirm the Duet gets the dashboard through Ethernet.
Restore remote connections afterward.

## Diagnostics / rollback

```sh
sudo systemctl status car-duet-ethernet-duet.timer --no-pager  # Duet
sudo systemctl status car-duet-ethernet-tiny.timer --no-pager  # Tiny
sudo journalctl -u car-duet-ethernet-duet.service -n 40 --no-pager
sudo journalctl -u car-duet-ethernet-tiny.service -n 40 --no-pager
```

To stop automatic setup, on the relevant machine:
```sh
sudo systemctl disable --now car-duet-ethernet-duet.timer
# or, on Tiny:
sudo systemctl disable --now car-duet-ethernet-tiny.timer
```
Then remove only the specific project-created NetworkManager connection if
needed, after confirming its name. Never remove active management interfaces.

The old `deploy/configure-tiny-usb.sh` remains for USB *gadget* setups and
must not be used with physical Ethernet.
