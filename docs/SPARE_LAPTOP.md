# Spare take-along laptop role

The spare is a ThinkBook 14-family laptop; exact model and Linux version are not
yet confirmed. Detect capabilities on the installed machine rather than assuming
all ThinkBooks support the same sleep/firmware settings.

From its normal user's checkout:

```sh
bash install.sh laptop
```

This is a separate role, not the Tiny or Duet installer. It configures:

- Car Ethernet address `172.16.42.4` on the central switch, peer discovery and
  automatic Wi-Fi-to-Ethernet Internet sharing. Gateway preference is Pi, Duet,
  then laptop; stale/disconnected sources expire. A laptop with working Wi-Fi
  keeps its own route and can supply the other components when elected. Saved
  Wi-Fi credentials and other connections are preserved.
- The same local shared archive and car-data services as supported car devices.
  Only managed car files are archived; unrelated personal files are not scanned.
- Managed fast-forward Git updates checked every 15 minutes over available IP
  connectivity. Local edits, changed origin/branch and failed tests stop updates;
  privileged role installation still verifies its exact immutable source.
- An automatic car-off deep-sleep monitor; no Tiny recorders, Home Assistant,
  fullscreen kiosk, desktop autologin or automatic remote-shell authorization.

Update the Pi, Tiny and Duet normally too: the Pi must learn the laptop's Ethernet MAC
and include it in its 30-second/150-second boot WOL bursts; Tiny's MooseFS exports
must recognize the new laptop address. Existing SSH/home-service authentication
still applies; software does not bypass passwords or invent credentials.

## Car-off sleep policy

The Pi currently loses power when the car turns off, so it cannot reliably send a
last shutdown command. The laptop instead requires all of these observations:

1. First, a confirmed car session: external power is present and the Pi's Ethernet
   status responder confirms the laptop's actual adapter MAC is registered.
2. Then both external power disappears and that registered Pi stops responding
   continuously for 90 seconds (minimum configurable delay: 30 seconds).
3. The lid is closed, the adapter identity is unchanged, the kernel exposes a supported
   memory sleep mode, and the Ethernet driver reports magic-packet wake support.
4. Application/update locks are clear and logind's normal sleep inhibitors permit
   sleep. Power/lid/Pi observations are checked again immediately before sleeping.

A missing Pi alone, a disconnected cable alone, missing sensor information or
normal battery-powered portable use does not trigger this policy. Taking the
laptop out and opening its lid ends the car session after the grace period.
After resume, the old car-off session is cleared so it cannot immediately sleep
again. No automatic boot-time standby is enabled for the laptop.

On systemd systems a low-level lid-switch inhibitor keeps the lid from suspending
the computer during an armed car session. It does not override application sleep
inhibitors, and is released when the session ends. Existing global lid and desktop
settings are preserved; logind is not restarted. If systemd-inhibit is unavailable,
the OS's existing lid behaviour still applies; no replacement is silently installed.

The monitor prefers Linux `deep` suspend-to-RAM. If the firmware exposes only
`s2idle` (common on newer laptops), it uses that available mode and reports it
explicitly; this may consume more battery than `deep`.
Sleep still consumes battery. This role does **not** create swap, change
hibernation/resume configuration, or enable unverified hibernation. Unsupported
suspend/WOL hardware stays awake with a pending status, rather than assuming it can
wake safely. Firmware "power on AC", waking from an empty battery, USB Ethernet
wake and actual closed-lid wake behaviour need physical testing. If an unknown
adapter or missing sensors prevent automatic sleep, use the laptop's normal manual
sleep/power controls until the hardware is verified.

Status:

```sh
cat /etc/car-dashboard/laptop-power-status.json
sudo journalctl -u car-laptop-power --no-pager -n 40
```

Status includes detected vendor/model, OS, external-power/lid state and the reason
sleep is pending. `physicalWakeVerified: false` explicitly distinguishes reported
NIC capability from a successful physical test. No sleep/network changes are made
to the cloud development host during automated tests.

Root-owned `/etc/car-dashboard/fleet.json` preserves explicit opt-outs:
`laptop_sleep: false` disables this monitor; `laptop_sleep_delay` changes the grace
period. Normal updates retain those values. Car-off sleep selects the best exposed memory mode only for that
sleep request and restores the previously selected memory-sleep mode on resume.
The spare remains removable: shared archives can depend on other powered car
nodes and are not a promise that every archived file follows the laptop offline.
