# Pi-controlled car startup over the central Ethernet switch

**Normal role installs now configure this automatically.** See [automatic device
setup](AUTOMATIC_DEVICE_SETUP.md) for the default path, including Wi-Fi sharing and
automatic MAC discovery. The standalone commands below are retained for advanced
manual installations and diagnostics; they are not required after normal setup.

The Pi, Tiny and Duet share a powered Ethernet switch. That LAN is the main path
for car communication; the old direct USB link is not required for this setup.
The standalone scripts below do not change Wi-Fi, IP addresses, gateways, DHCP,
NAT or Tailscale; the automatic role manager described above manages the car LAN
and sharing routes itself.
Internet sharing from Pi or Duet can be configured independently; use one active
Internet gateway/DHCP authority per LAN, not competing Pi and Duet gateways.
Wake-on-LAN does not need Internet, DHCP, DNS, Home Assistant or LoRaWAN. The radio
can later be on either Pi or Tiny without changing this startup schedule.

## Startup sequence

1. Vehicle power brings up Pi and switch. Tiny/Duet **firmware** must separately
   support automatically starting after power/charging is applied.
2. An explicitly armed target considers suspend once per boot, starting around
   15 seconds after Linux starts. It enables Ethernet magic-packet wake, checks
   the physical link, and waits for known car updater locks to be free.
3. At **30 seconds after Pi Linux boot**, Pi sends three WOL packets to each
   configured Ethernet MAC. The second burst is at **150 seconds after boot**
   (two minutes after the first), even if the first attempt appeared successful.
4. A resumed target stays awake for the rest of that boot. No inactivity timeout
   or recurring auto-suspend is introduced.

Linux cannot measure the time spent in firmware before it started, so these are
boot-relative times, not precise times from physical voltage application. Network
or service delays can delay packet transmission; a missing link at 30 seconds can
be recovered by the independent 150-second attempt. A sent packet is not a wake
confirmation. If a target has not entered standby by its own 120-second boot
cutoff, it stays awake. Before sleeping it also queries Pi's local wake-window
responder and stays awake if Pi's uptime is 140 seconds or more, or the timing
check fails. This avoids sleeping after a much earlier-booted Pi has sent both
wake attempts; the remaining ten-second margin must cover the target's measured
suspend latency.
If Pi takes far longer to boot, the devices can spend longer asleep before it wakes
them. This is temporary boot standby, not a solution for ignition-off power loss.

## Hardware requirements — verify before arming

- Tiny firmware: set **Restore on AC Power Loss / After Power Failure → Power On**
  (names vary). Enable PCIe/LAN wake from the desired sleep state; a firmware
  “deep off/ErP” option may disable NIC standby power. Software running in Linux
  cannot universally change these firmware settings.
- Duet: verify that the actual model boots when charging power returns, and that
  its particular Ethernet adapter can wake the tablet from suspend. ARM tablet
  boot-on-charge support varies. A USB Ethernet dongle reporting `Supports Wake-on:
  g` alone does **not** prove that the USB controller/port remains powered or that
  its wake event reaches the tablet.
- Use real, stable **Ethernet adapter MACs**, not Wi-Fi or Tailscale MACs. Avoid a
  randomized adapter MAC that changes on each boot. Switch and target NIC must
  remain powered while sleeping. A USB gadget that disappears during suspend
  cannot receive normal Ethernet WOL. Wi-Fi needs separate WoWLAN support and is
  intentionally rejected by this Ethernet standby installer.
- Suspend-to-RAM keeps memory and the wake NIC powered. `--deep` requests the
  kernel's deep suspend when available; unsupported deep suspend is rejected.
  The default keeps the kernel's current suspend mode. Neither option can promise
  the absolute minimum consumption; measure it on the actual hardware. Soft-off
  might use less power on some PCs, but is not configured here because automatic
  tablet boot and wake-from-off are not established.

Do the first suspend/wake test with physical access to the device. Suspend drops
SSH temporarily; an unsupported adapter can require the power button to recover.
Normal kernel/logind inhibitors remain in effect. Known courier downloads and
transfers, and Tiny's managed update lock, delay automatic standby; arbitrary OS
updates and third-party software need their own normal sleep inhibitors.
No values/locations are recorded while Tiny itself is suspended.

## 1. Identify the Ethernet adapters

On each target, with its cable connected to the switch:

```sh
ip -br link
```

For its Ethernet interface (replace `eth0` with the actual name):

```sh
cat /sys/class/net/eth0/address
sudo ethtool eth0
```

Record the MAC and verify magic-packet support (`g`). Use the **Pi's** outgoing
Ethernet interface in the sender configuration. It can have a different name from
the target interface. The latest full Tiny runtime bundle carries `ethtool`; if
it is not yet installed, install it with your OS package manager when online or
complete the current runtime/courier upgrade first. Duet needs its own native
`ethtool` package (`apt install ethtool` or `apk add ethtool`).

## 2. Install the Pi sender

On Raspberry Pi OS/systemd, with this repository checked out:

```sh
cd ~/car-dashboard
cp power/pi-wake.example.json ~/pi-wake.json
nano ~/pi-wake.json
bash power/install-pi-wake.sh ~/pi-wake.json
```

Replace **both example MAC addresses**. The installer rejects unchanged example
MACs. Set `broadcast` to the real switch LAN's broadcast address and `interface`
to Pi's Ethernet interface. Set `status.bind` to Pi's actual static Ethernet
IPv4 address and `status.interface` to that same adapter. For the existing `172.16.42.0/24` LAN, use
`172.16.42.255`; Tiny can retain `.2` and Duet `.1`. Pi needs a unique address
on that same LAN (for example `.3` if unused). These addresses are examples;
this installer deliberately does not assign them or change your Internet route.
Do not use a Tailscale address as a WOL destination. Broadcast packets normally
stay in their local subnet; routed Tailscale/Internet WOL is not configured.

Two systemd timers and the Ethernet-only timing responder are enabled for the
**next Pi boot**. Installation itself does
not send wake packets. Test manually, or inspect the timers and boot logs:

```sh
sudo systemctl start car-wake@manual.service
systemctl list-timers 'car-wake-*' --all --no-pager
sudo journalctl -b -u car-wake@first -u car-wake@retry --no-pager
```

The target queries Pi's timing responder using UDP port **8799** on the switch
LAN. Allow that port through Pi's local firewall for the car LAN if needed. This
small response contains only boot uptime and the last scheduled wake time, not
vehicle data. The responder is bound to the configured Pi Ethernet address and
interface; failed queries leave targets awake. It is not exposed on Wi-Fi or
Tailscale. It is a trusted-LAN timing hint, not authenticated power control.

The sender uses Python's standard library and UDP broadcast; no `wakeonlan`
package is needed. An explicitly chosen Pi interface is bound using CAP_NET_RAW;
the sender otherwise runs under an ephemeral unprivileged systemd account.
The target MAC is not an authentication secret; WOL is not an authenticated remote
power-control protocol. Keep wake packets on your trusted car LAN.

## 3. Install target capability checks, initially unarmed

Run as the normal user on Tiny and on Duet, changing role/interface and Pi's Ethernet IP as appropriate:

```sh
cd ~/car-dashboard
bash power/install-boot-standby.sh tiny enp1s0 172.16.42.3
# On Duet, for example:
# bash power/install-boot-standby.sh duet eth0 172.16.42.3
```

This checks capabilities and installs root-owned code/configuration. It does not
suspend the current session and leaves automatic boot suspend **unarmed**. An
unsupported target remains awake; it is not silently forced into a sleep state
from which the Pi cannot wake it. Supported init systems are systemd or OpenRC
with elogind/`loginctl`. Plain OpenRC without a suspend manager is rejected.
For Duet, install its normal courier first so update locks exist.

From a **local console**, request one test suspend:

```sh
sudo python3 -I /usr/local/lib/car-dashboard/boot-standby.py test-suspend
```

While it sleeps, issue `sudo systemctl start car-wake@manual.service` on Pi.
Confirm **both** devices actually wake; inspect Ethernet/USB behavior and power
consumption. If the check or wake test fails on Duet, leave it awake, use its power
button, or select a proven wake-capable adapter/firmware solution.

## 4. Arm once physical wake works

On each proven target, rerun its installer with `--arm`:

```sh
bash power/install-boot-standby.sh tiny enp1s0 172.16.42.3 --arm --deep
# Duet, if its hardware supports wake but not deep sleep:
# bash power/install-boot-standby.sh duet eth0 172.16.42.3 --arm
```

Omit `--deep` if the kernel does not expose that state. Arming enables the next
boot behavior; it does not suspend immediately. On systemd the timer is enabled;
on OpenRC a single background boot service is registered, with no respawn loop.
Restarting the entire service manually is not part of normal operation.
A successful suspend/resume is checked with Linux clocks; updater locks remain
held while waiting for resume, even if logind acknowledges the request early.

To disarm, rerun the same installer **without `--arm`**, or change `armed` to
`false` in `/etc/car-dashboard/boot-standby.json`. On systemd, you can immediately
stop/disable the next-boot timer with:

```sh
sudo systemctl disable --now car-boot-standby.timer
```

On OpenRC: `sudo rc-update del car-boot-standby default`. For a physically
unreachable already-sleeping target, disarming cannot wake it; send a known-working
magic packet or use its power button first.

The standalone power installer remains unarmed unless requested. Normal role
install/update instead enables supported standby automatically, with updater,
link, wake-registration and Pi timing checks. Unsupported hardware stays awake.
Source updates arrive through the normal Git/courier path; rerun the role's power
installer to refresh its installed root-owned helper and units.

## Power off still needs retained power

Without a UPS/ignition-delay controller, the car can remove power while a target
is awake or updating. WOL/standby changes do not fix that. Keep using
[power-loss protections](POWER_LOSS.md), and retain power long enough for a normal
shutdown when you add the controller. The Duet's internal battery behavior also
needs to be considered independently from Tiny's AC supply.

Automated tests cover packets, interface selection, independent target errors,
update-lock contention, missed-boot cutoff and one-time resume. Generated systemd
units are syntax-checked. No physical Tiny/Duet/Pi wake or power-on claim is made
until tested on those devices.
