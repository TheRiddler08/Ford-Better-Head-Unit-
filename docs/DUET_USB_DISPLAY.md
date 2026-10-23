# Wired Lenovo Duet 3 display (USB, no Wi-Fi needed)

This optional deployment splits the head unit into two computers:

- **ThinkCentre M910q Tiny (Ubuntu)** runs the vehicle recorder, SQLite,
  GPS source, dashboard server, offline maps, update agent and other services.
- **Lenovo Duet 3 11Q727 (postmarketOS / Linux, ARM64)** is a touchscreen client
  running a browser. It has no direct OBD access or vehicle ECU controls.

## What the USB cable does

The Duet's USB-C gadget/device controller presents an Ethernet interface to
the Tiny. The two computers exchange ordinary network packets **over USB**,
without joining any Wi-Fi access point or requiring internet. There is no video
signal over the cable. Browser rendering and touch handling run on the Duet.

The device-specific postmarketOS wiki currently lists USB networking and role
switching as working for `google-wormdingler`, but kernel/image regressions
are possible. Check your exact Chromebook model first. Install and test Linux
on the tablet using the upstream postmarketOS device guidance; these scripts
**do not flash or replace ChromeOS**.

PostmarketOS USB gadget defaults commonly use:
- Duet: `172.16.42.1`
- Tiny: `172.16.42.2`

Check `ip -br addr` on both sides after connecting a *USB data cable*;
these addresses are assumptions to verify, not guaranteed across Linux images.
A second charger may be needed for the Duet; simultaneous charging and data
must be tested on the actual hardware. Do not rely on a Tiny USB port to supply
unlimited tablet power.

## 1. Prepare the Tiny over SSH (do not disconnect your existing network)

Confirm the dashboard is still listening only on loopback:

```bash
grep -E '^(CAR_BIND|CAR_PORT)=' /etc/car-dashboard/dashboard.env
curl -fsS http://127.0.0.1:8787/api/health
```

Install OpenSSH server if not already running:

```bash
sudo apt-get install openssh-server
sudo systemctl enable --now ssh
```

After the Duet is running postmarketOS with USB gadget networking and its
USB-C port is connected to the Tiny, use:

```bash
ip -br link
readlink -f /sys/class/net/YOUR_USB_INTERFACE/device/driver
cd ~/car-dashboard
bash deploy/configure-tiny-usb.sh YOUR_USB_INTERFACE
ping -c 2 172.16.42.1
```

Only pass the **USB gadget Ethernet** interface, not the Tiny's physical
Ethernet or Wi-Fi. The script checks for a recognized USB network driver before
creating a static NetworkManager profile. It does not add a default route or
modify the Tiny's ordinary network interfaces. If the USB interface name
changes after unplugging, inspect `nmcli con show drive-duet-usb` and
reconfigure for the new name rather than blindly selecting another interface.

Do not change `CAR_BIND` from `127.0.0.1`; the Duet connects via a restricted
SSH forward to keep the dashboard inaccessible directly on the USB subnet.

## 2. Install the Duet display client on postmarketOS

On the Duet's Linux desktop/terminal (not on the Tiny):

```sh
sudo apk add bash python3 openssh-client firefox git
git clone https://github.com/TheRiddler08/Ford-Better-Head-Unit-.git ~/car-dashboard
cd ~/car-dashboard
bash duet/install.sh
```

If `sudo` is not installed, use the postmarketOS-supported privileged package
manager invocation instead. Initial packages/source need internet or an
offline package transfer; **normal driving does not require internet**.

The installer copies the proxy and launcher into
`~/.local/share/drive-console/`, creates a Duet-specific SSH key, and adds
`~/.config/autostart/drive-console-duet.desktop`. It does **not** change
ChromeOS, firmware, or the Tiny's configuration.

It prints the contents of `~/.ssh/drive-console-duet.pub`. Copy that **public
key only** to a file on the Tiny (for example, `/tmp/drive-duet.pub`). The
**private key must never leave the Duet**.

On the Tiny:

```bash
cd ~/car-dashboard
bash deploy/authorize-duet-key.sh /tmp/drive-duet.pub
```

The helper adds a narrowly restricted SSH key to the Tiny account that may
forward only to the Tiny's loopback dashboard port `127.0.0.1:8787`.

### Verify the SSH host key before first connection

On the Tiny:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On the Duet:

```bash
ssh-keyscan -t ed25519 172.16.42.2 | ssh-keygen -lf -
```

Compare the fingerprint **out of band** with the Tiny's known value. Only if
they match, on the Duet:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keyscan -H -t ed25519 172.16.42.2 >> ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
```

Never use `StrictHostKeyChecking=no` to bypass this step.

## 3. Run the touchscreen kiosk

Log into the Duet's *graphical* Linux desktop (X11/Wayland). Either log out
and back in to trigger autostart or execute:

```bash
~/.local/share/drive-console/launch.sh
```

The local page is `http://127.0.0.1:8877/` **on the Duet**. The launcher starts
a loopback-only proxy, automatically reconnects the SSH tunnel after USB
disconnects/reboots, and launches a fullscreen browser. During a broken link
the tablet shows a retry page; it does not claim that live OBD/GPS data exists.

No vehicle data is saved on the tablet by this proxy. The source of truth
remains the Tiny. Saved schedules and other head-unit UI features on the Tiny
will be visible through the same URL as they are added.

Check:

```bash
# On the Duet:
curl -i http://127.0.0.1:8877/__link
tail -n 60 ~/.local/state/drive-console/kiosk.log

# On the Tiny:
curl -fsS http://127.0.0.1:8787/api/health
sudo systemctl status car-dashboard.service car-obd-monitor.service --no-pager
```

If `/__link` returns HTTP 503, check the USB subnet first, then SSH host key
verification, authorized key, and the Tiny's SSH service.

## Transition and recovery

Keep the Tiny's existing local kiosk available until the Duet hardware
connection has been tested. Optionally disable the Tiny's graphical kiosk with
`bash deploy/disable-kiosk.sh` once the Duet works, but keep the Tiny's
dashboard/recorder services installed. Avoid rebooting the Tiny remotely during
the first migration.

For local fallback, SSH to the Tiny and use
`http://127.0.0.1:8787/` on a browser running on the Tiny itself. Future
dashboard code is installed **on the Tiny**, not separately on the Duet.
The Duet launcher/proxy can be refreshed after a repo update by running
`bash duet/install.sh` again on the Duet, using an up-to-date checkout.
The updater is not yet a fully automatic dual-device deployment system.

### Limitations

- This is application UI over USB networking, not direct HDMI/DisplayPort.
- Navigation still requires a routing backend for genuine route-based ETAs.
- Browser-local files selected for playback stay on the tablet and may not
  automatically play through the Tiny's audio output; car audio routing needs
  separate configuration.
- The interface stays read-only toward vehicle OBD/ECU.
- The original dashboard has no user authentication, so the forwarding key
  must stay private and the Tiny's port 8787 must stay loopback-only.
