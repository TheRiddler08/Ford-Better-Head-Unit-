# Automatic setup on install and update

The normal role installer now owns car network sharing, discovery and supported
boot standby. These are defaults, not a separate post-install checklist. Existing
settings in `/etc/car-dashboard/fleet.json` are preserved, including explicit
opt-outs. No Wi-Fi password, device MAC address or per-boot sharing command needs
to be added to GitHub.

## Ordinary installation/update

Run as the normal device user from its checkout. Initial privileged setup asks
for sudo once as needed:

```sh
cd ~/car-dashboard
git pull --ff-only
bash install.sh duet   # Duet: display, courier, network and power role
# On Tiny: bash install.sh tiny
# On Pi:   bash install.sh pi
# On spare laptop: bash install.sh laptop
```

The existing `duet/prepare-update.sh`, `duet/install.sh`,
`duet/install-courier.sh`, and Tiny's `deploy/install.sh` also call the automatic
role installer. You do not need to switch commands to get the defaults. Pi's
`pi/install.sh` is its lightweight role installer; it does not install Tiny's
Home Assistant container or vehicle recorders on Pi.

Pi defaults to a mount-only storage client for its 8 GB microSD card: bulk data
goes to the Tiny/Duet MooseFS pool, with no SD archive contribution or metadata
backup. See [Pi small-card storage](PI_SMALL_CARD.md) for bounded durable state,
RAM logs, offline behavior and update commands.

An old, unpaired Tiny gets its receiver and role bootstrap through the existing
Duet reconnect/pairing script. Tiny does not need Internet. The full signed courier
bundle carries the native Ubuntu networking dependencies too. An old receiver
that first installs the new software but has not applied role settings is detected
by its receipt: Duet automatically sends the same payload again until the current
receiver finishes role setup. This does not relax SSH or signature verification.
The initial normal SSH/sudo authorization for a previously unpaired Tiny is still
required; a software update cannot bypass machine authentication.

After initial setup, root-owned role code reconciles settings every 30 seconds:

- Duet follows the **signed ready courier bundle**, extracting only the known
  installer source paths from its exact Git commit. It verifies the signature
  against the courier key recorded during initial setup. It does not execute
  arbitrary editable working-tree files as root.
- Tiny applies role code from the verified immutable commit in each carried
  update and watches subsequent managed Git changes. Carried updates do not
  require Internet, apt mirrors, a Docker registry, or a second update command.
  Tiny also enables its guarded Git updater by default when Internet is available
  through Pi, Duet or its own connection, sharing the same update lock. Its
  deliberately stopped dashboard stays stopped. The disable-updates command
  records an explicit opt-out so later role updates do not turn it back on.
- Pi checks the repository's `main` branch every 15 minutes while its own Wi-Fi
  Internet works. It requires a clean `main` checkout and the official origin,
  fast-forwards only, runs the tests as the ordinary user, and restores the old
  checkout if candidate tests fail. Its installed role code then updates too.

Network/power roles are root-owned code. Repository write access and the Duet
courier signing key therefore authorize changes to privileged installation,
routing and power behavior. Protect that account and key as already required for
runtime/receiver updates. No peer announcement authorizes executing commands.

## Switch LAN and automatic Internet sharing

The central switch is the main car connection:

| Device | Car Ethernet IPv4 | Role |
| --- | --- | --- |
| Duet | `172.16.42.1/24` | Display, update carrier, backup Wi-Fi gateway |
| Tiny | `172.16.42.2/24` | Home Assistant, vehicle/GPS recording, LAN client |
| Pi | `172.16.42.3/24` | Wake coordinator, preferred Wi-Fi gateway |

A single physical Ethernet adapter is selected automatically. Existing car
addresses and legacy managed profiles are retained. A previously addressed adapter
gets the car identity as an additional address without deleting its old lease,
Wi-Fi or default route. A blank adapter gets a persistent NetworkManager car
profile. The running service repairs the car identity after reconnects. If there
are multiple plausible Ethernet adapters, it reports that ambiguity and waits
instead of reconfiguring an unknown management port. An explicit `interface`
setting in the root-owned role configuration resolves unusual multi-NIC setups.

Connect Pi and/or Duet to an already saved Wi-Fi/hotspot normally. NetworkManager
handles saved credentials and reconnects. The role service then:

1. Tests actual IPv4 HTTPS access bound to each Wi-Fi interface, retaining TLS
   verification. Traffic already shared by a peer cannot be advertised back as a
   fresh Wi-Fi uplink. If destination DNS fails, a numeric HTTPS fallback avoids
   depending on a disconnected car DNS gateway; TLS and proxy settings remain.
2. Advertises gateway readiness on the physical Ethernet LAN. Pi wins when both
   uplinks are usable; Duet takes over when Pi's uplink disappears. Peer readiness
   expires after 15 seconds; probe/advertisement intervals add a few seconds to
   practical failover. Existing Internet sessions can reconnect during a gateway
   change; this is not session-preserving routing.
3. Starts IPv4 forwarding, subnet-scoped NAT/forwarding rules in its own iptables
   chains and an isolated DNS-only dnsmasq process on the elected gateway.
4. Sets a tagged car default route/DNS on devices without working local Wi-Fi,
   withdrawing it when it is no longer needed. Existing Wi-Fi profiles and
   Tailscale settings are not rewritten. Tailscale's more-specific DNS domains
   remain separate on systemd-resolved systems. While using a shared gateway,
   route/DNS state is checked every ten seconds and repaired after NetworkManager
   resets it during Ethernet reconnects, even if the elected gateway is unchanged.

Pending optional storage packages are retried without restarting unchanged
network/wake/worker services. Earlier versions reapplied the whole role every
30 seconds when MooseFS tools were missing, briefly dropping NAT and DNS each
time. Changed code/configuration still applies, and manually re-running the role
installer explicitly repairs/restarts its services.

`wifiInternet: true` with `gateway: duet` means Duet has selected its own Wi-Fi
uplink. It does not prove a client has an Internet route or that an additional
firewall permits forwarding. An empty `peers` map means no valid car Ethernet
announcements have been received; check the physical switch and peer role services.
Missing MooseFS tools affect shared storage, not the basic Wi-Fi sharing path.

There is **no DHCP server** on either role and no NetworkManager `shared` profile,
so Pi and Duet cannot hand out competing leases. During brief peer-discovery gaps,
both can transiently offer NAT on their different fixed addresses; clients use the
deterministic preferred gateway once peer state arrives. The car LAN remains
usable without Wi-Fi. Internet sharing covers IPv4; it does not add IPv6 routing,
bridge hotspot networks, or share Tailscale itself as an uplink.

The role code changes only its own firewall chains and tagged routes; it does not
flush the system firewall, change Docker/Tailscale chains, or change global DROP
policies. Existing custom nftables/other security policies can still block
forwarding; those need to permit the intended car LAN path. Automatic setup cannot
safely erase an unknown custom security policy. Missing packages, DNS bind
conflicts, unavailable adapters and setup errors are reported and retried.

Supported systems use NetworkManager with systemd or OpenRC. Raspberry Pi OS
Bookworm or newer is the expected Pi base. A historical/custom Pi network manager
cannot be blindly replaced without affecting existing access. OpenRC Duet builds
use the native Alpine packages and elogind for suspend. Only packages missing from
the role are requested. Tiny's full offline Ubuntu bundle includes them; a legacy
Git-only bundle cannot install missing OS packages while offline.

## Automatic wake discovery and supported low-power boot

Peers announce their real Ethernet adapter MAC addresses over the switch. Pi
keeps learned MACs across boots and generates its wake configuration itself.
It sends wake bursts at **30 seconds** and **150 seconds after Linux boots**.
Physical power-to-firmware startup time is outside that Linux timer.

Tiny and Duet automatically prepare once-per-boot standby when the kernel,
physical Ethernet driver and suspend manager report the necessary capabilities.
It is enabled for the **next boot** and never deliberately suspends the SSH
session performing installation. The current suspend mode is retained; an
unsupported mode is not forced. Devices remain awake if the adapter lacks WOL,
the link is down, a managed update is busy, Pi has not registered their actual MAC,
or the scheduled wake window cannot be confirmed. An already resumed device stays
awake for that boot. The Pi timing check prevents a slow-booting target from
sleeping after Pi's last wake burst.

Reported USB-adapter WOL support still cannot prove that tablet firmware/USB power
will deliver its wake event. The first actual boot/wake cycle needs supervision
and a power-button recovery path. Linux cannot universally set Lenovo firmware
“power on after AC returns” or add missing Duet boot-on-charge support. Those
hardware capabilities/settings remain explicit requirements, not settings the
installer can truthfully claim to have changed. Likewise, retained power is still
required for clean ignition-off shutdown; see [power-loss protection](POWER_LOSS.md).

NTP is enabled on systemd devices. A numeric fallback time-server configuration
helps RTC-less devices recover without first resolving an NTP hostname through
DNSSEC. Existing timezone and explicitly configured primary NTP servers are
preserved. LoRaWAN stays unconfigured until its location/transport is decided;
there is no guessed radio device or vehicle write/control configuration.

## Status and explicit overrides

On any device:

```sh
python3 /usr/local/lib/car-dashboard/fleet-current/fleet.py status
```

Tiny's **Updates → Car network** also shows setup phase and selected gateway.
Systemd logs: `sudo journalctl -u car-fleet -u car-fleet-refresh -n 60 --no-pager`.
OpenRC logs are `/var/log/car-fleet.log` and `/var/log/car-fleet-refresh.log`.

Advanced overrides live in `/etc/car-dashboard/fleet.json`:

- `enabled: false` disables automatic role services when reapplied.
- `boot_standby: false` preserves an always-awake target.
- `interface: "eth0"` pins an otherwise ambiguous Ethernet adapter.
- Pi `auto_update: false` disables its Internet Git checks.
- Tiny `app_updates: false` or the normal disable-updates command preserves a
  carried-updates-only setup.

Reapplying the ordinary role installer preserves these choices. No Wi-Fi secrets
or SSH passwords belong in that JSON or the repository. Software-only validation
covers election, scoped routes/NAT/DNS, authenticated updater handoff, immutable
installer publication, wake registration and migration receipts. Physical switch,
Wi-Fi failover, tablet suspend and firmware boot behavior still need validation
on the actual devices.

The normal role installer also enables the [resumable software link and activation services](RESUMABLE_UPDATES.md). They wait for home authentication/radio configuration when missing; they do not guess credentials or serial ports. Duet source checkouts now advance automatically after tests, and active display helpers restart with rollback.

The removable spare laptop has a separate [.4 role and car-off sleep policy](SPARE_LAPTOP.md).
