# Duet 3 low-power car display — feasibility and staged deployment

This system has **two independent computers**. The ThinkCentre Tiny runs the
dashboard, OBD2, GPS and update services. The Wormdingler Duet 3 runs only a
browser kiosk and its encrypted local SSH proxy over the private Ethernet cable.

## Critical power constraint

A *fully powered-off* Tiny cannot emit a WoL magic packet. A *suspended* Duet
cannot receive a network packet unless its USB Ethernet device, host controller,
and platform wake wiring remain powered and support remote wake. An ASIX
`ax88179_178a` driver's ethtool WoL support is **not** proof that the ARM
Chromebook can resume from suspend over this USB adapter.

For maximum reliability, obtain an ignition/accessory signal through a fused,
electrically isolated automotive-rated input or an ignition-aware power
controller. The controller should distinguish ignition from temporary USB-C
power interruptions and provide regulated power. Do not tap raw 12 V into GPIO
or USB. Do not rely on the dashboard's HTTP availability as the sole indication
of ignition: a software crash should not turn off the user's screen.

## Safe first stage

Run `bash duet/power-audit.sh eth0` on the Duet. It **only reads** Linux
sysfs and ethtool information: battery/charger status, supported suspend modes,
USB wakeup configuration and network wake support.

Do not set an automatic suspend timer yet. While connected to external power,
with SSH access verified from a second device, manually test suspend/resume
using a local power button, followed by USB-Ethernet remote wake if supported.
Verify that touch, display, battery reporting, USB-C charging, network and
Firefox work after resume **several times**, including a warm reboot of Tiny
and full vehicle power-cycle simulation.

If WoL is unsupported, the safe software-only baseline is:
- Keep the Duet awake but turn its display brightness down/off after a
  verified prolonged absence of ignition or head-unit heartbeat.
- Wake/unblank on local touch or power button; reconnect automatically when
  Tiny becomes available. This *still uses power* and is not deep sleep.
- Or allow Duet to suspend and use its own power button to resume; a physical
  ignition-aware wake controller is needed for truly unattended wake if the
  platform cannot remote-wake.

## Proposed ignition-aware state machine (not enabled yet)

- **DRIVING:** Tiny is running, ignition verified on, display awake, updates
  delivered in place. Neither host goes to sleep just because one heartbeat
  is briefly missing.
- **PARKED-GRACE:** Ignition off, wait configurable grace period (e.g. 2–5
  minutes) for accessory use, UI shutdown, and durable writes.
- **STANDBY:** Sleep the Duet only after a tested reliable wake route exists;
  never sleep solely because Tiny is temporarily offline. Use power-aware,
  low-battery cutoffs so the car's 12 V battery is never deeply discharged.
- **RESUME:** Ignition asserted and Tiny restored to power; display resumes,
  link renegotiates and tunnel reconnects, with retries that don't change the
  known-good Ethernet and Tailscale profiles.
- **RECOVERY:** A failed watchdog or missed wake falls back to local power
  button/ignition controller, **not** endless reboot loops.

## Firmware startup delay

The read-only 8 MiB Wormdingler SPI firmware backup has existing GBB flags
`0x00000000`. The GBB is at offset `0x003e1000` and is located inside
`WP_RO` (the lower `0x00400000` bytes, hardware protected according to
`flashrom -p linux_mtd --wp-status`). The short developer-mode flag
`GBB_FLAG_DEV_SCREEN_SHORT_DELAY` is bit `0x1`, but **it cannot be
applied safely while hardware protection is active**. No firmware writes or
write-protection changes are performed by this repository.

A 2-second *developer-mode warning delay* does not mean a 2-second cold boot.
If working standby/wake is confirmed, normal car usage should resume without
passing through the firmware warning screen at all.

## Tiny wake-on-LAN

A powered-off Tiny may be wakeable by WoL on its **built-in wired NIC** only
when BIOS/UEFI and standby power support it. Test with `ethtool` and BIOS
settings; WoL on the dedicated USB Ethernet dongle may not work when powered
off. The Duet can send a wake packet only while awake. If both systems suspend
and neither can receive wake signals, they deadlock. Prefer ignition-switched
power or an always-on ultra-low-power controller for the boot signal.

## Before enabling automation

1. Get `duet/power-audit.sh` results (especially `/sys/power/mem_sleep`,
   `ethtool eth0`, USB wakeup paths, charger capacity/online).
2. Confirm how the vehicle will provide a **verified ignition/ACC signal** and
   continuous standby charging, with voltage monitoring and cutoff.
3. Prove reliable suspend/resume in several cycles and test cold start, warm
   start, unplugged charger, Ethernet disconnect and low battery.
4. Only then introduce a service/timer that controls idle/suspend. Avoid
   accidental battery drain, permanent suspend, and breakage of remote access.
