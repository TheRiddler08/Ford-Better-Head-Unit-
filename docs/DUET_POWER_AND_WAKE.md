# Duet 3 power: what is automatic and what firmware decides

The Wormdingler Duet 3 is the car display, update carrier and USB hub host for
the GPS and LoRaWAN modem. Its power behaviour is installed by the normal Duet
role install/update; no separate command is required.

## Automatic today

- **Startup:** Tiny and Duet start straight up on car power and stay on. They do
  not suspend at boot to wait for Pi's Wake-on-LAN (see
  [automatic device setup](AUTOMATIC_DEVICE_SETUP.md#startup-on-car-power)).
- **Car off:** `car-duet-power` treats external (USB-C/hub pass-through) power as
  the car signal. After car power has been seen, removing it starts a 150-second
  countdown and then a normal power-off. At or below 10% battery it powers off
  immediately, including if the battery reaches 10% during the countdown. Power
  returning cancels the countdown. A Duet started on battery without car power is
  never powered off. The network is never used as the car-off signal. Details and
  opt-out: [Pi wake and power](PI_WAKE_POWER.md#duet-car-off-power-off).

## Decided by firmware, not Linux

- **Turning on when car power returns.** Linux cannot enable this on the Duet.
  Test once: let it power itself off, then restore car power. If it does not
  start, it needs its power button (or an ignition-aware power controller) after
  each power-off, or set `"duet_poweroff": false` to keep it on instead.
- **Suspend/remote wake.** USB Ethernet `ethtool` Wake-on support is not proof
  that the ARM tablet resumes from suspend on a packet. Nothing here suspends the
  Duet; it powers off instead.
- **Firmware warning delay.** The read-only Wormdingler SPI backup showed GBB
  flags `0x00000000`, with the GBB inside the hardware write-protected `WP_RO`
  region. `GBB_FLAG_DEV_SCREEN_SHORT_DELAY` (bit `0x1`) cannot be applied safely
  while write protection is active. This repository performs no firmware writes or
  write-protection changes.

## Read-only power check

```sh
bash duet/power-audit.sh eth0
```

It only reads sysfs/ethtool: board, suspend modes, every power supply's type,
status, capacity and `online` state (the values `car-duet-power` uses), and USB
Ethernet wake settings. It never writes sysfs, changes firmware or suspends.
Use the hub's Ethernet interface name if it is not `eth0`.

## Hardware notes

For a verified ignition/ACC signal, use a fused, isolated automotive-rated input
or an ignition-aware power controller, never raw 12 V into USB/GPIO. A controller
can also provide boot-on-ignition and protect the car's 12 V battery. USB-C power
interruptions while cranking are tolerated by the countdown.
