# Changing the car's Wi-Fi network

Connect the Duet to the new Wi-Fi using its normal desktop settings. Keep the
Duet, Pi and Tiny connected to the powered car Ethernet switch. The Tiny does
not need its own Wi-Fi adapter: it uses the elected Pi/Duet gateway.

Update and apply the Duet role from a normal Duet terminal:

```sh
cd ~/car-dashboard
git pull --ff-only
bash deploy/install-fleet.sh duet
```

This repairs Ethernet Internet sharing immediately; it doesn't wait for Tiny to
download anything. Tiny software updates continue through the signed Duet courier.

## Pair the Pi once, even while its Wi-Fi is offline

```sh
bash duet/setup-wifi-sync.sh cayden@172.16.42.3
```

Use the Pi's actual Linux username if it differs. The Pi must already have its
car role installed, be reachable at `.3`, and accept the normal administrative
SSH/sudo login. Confirm its host fingerprint normally. This one-time operation
needs Duet sudo, Pi SSH authentication and Pi sudo; subsequent transfers have no
password prompts. No Wi-Fi password needs to be entered in a terminal or chat.

The script copies only a small credential receiver over Ethernet, so Pi Internet
isn't a prerequisite. It pins the host key obtained through that verified login
and authorizes a separate restricted key. Normal Pi role updates subsequently
replace the bootstrap receiver with immutable verified installer code.

The same command can pair an optional Linux laptop at `.4`, or Tiny at `.2` if
it later gets Wi-Fi. Existing courier/display keys are never repurposed.

## Automatic behavior

- Every 30 seconds, the Duet sends saved, autoconnect-enabled WPA2-PSK/WPA3-SAE
  personal networks to explicitly paired car peers over encrypted SSH. Open,
  enterprise/certificate, hotspot/AP and desktop-keyring-only profiles are
  skipped. A user-only secret must first be saved for system use in the normal
  NetworkManager settings; the service never prompts/unlocks a desktop keyring.
  `profiles-unavailable` or `active-profile-unavailable` in status means the
  current network could not be exported; it is not reported as successfully shared.
- Passwords use SSH stdin and root-only NetworkManager keyfiles. They are absent
  from process arguments, Git, discovery broadcasts, HA status and radio traffic.
  This is separate from LoRaWAN and works without the home server.
- Existing local profiles are retained. A managed profile uses automatic IP/DNS,
  independent device identity and lower autoconnect priority, so it can recover a
  changed Wi-Fi password without overwriting the old profile. An active connection,
  disabled Wi-Fi radio/device, or per-profile autoconnect opt-out is respected.
  Only a disconnected, autoconnect-enabled Wi-Fi device is asked to reconnect.
- Pi remains the preferred gateway, followed by Duet and the optional laptop.
  Sharing checks scoped firewall rules every 15 seconds, including native
  nftables input/forward chains with DROP policies. It uses its own native NAT
  table when available and repairs firewall reloads without restarting healthy
  DNS. IPv4 forwarding/DNS changes stay scoped to car Ethernet; Docker/Tailscale
  chains, unrelated firewall rules and other default routes are retained.
- A shared gateway's managed route/DNS is rechecked every ten seconds. New uplink
  detection, scans, Wi-Fi authentication and gateway election can take longer;
  existing TCP sessions may reconnect when the gateway changes.

## Status and opt-outs

On Duet:

```sh
cat /run/car-dashboard/wifi-sync-status.json
sudo journalctl -u car-wifi-sync -u car-fleet -n 40 --no-pager
```

On Pi, the same status file reports profile receipt. On OpenRC systems use
`sudo rc-service car-wifi-sync status` on Duet and `sudo rc-service car-fleet
status` for sharing. HA's Devices / Network card displays counts/phases and peer
service beacons; it never exposes network names or passwords.

Set `"wifi_sync": false` in `/etc/car-dashboard/fleet.json` on either endpoint
to stop sending/accepting profiles. Role reconciliation preserves the setting;
the sender/receiver also reads it on each transfer. Disable autoconnect for an
individual imported network in NetworkManager to keep it off. Received profiles
are not deleted if the Duet forgets a network or pairing is removed.

Enrollment records, the dedicated private key and pinned peer host keys live in
root-only `/etc/car-dashboard/wifi-sync/`. The receiver key is restricted to
source `172.16.42.1` and the fixed no-argument profile importer, with no shell,
PTY, port, agent or X11 forwarding. Explicit admin repair is required for a
changed SSH host key. To stop future transfers to a peer, remove its role entry
from `peers.json` as root; existing networks on that peer remain available.

Software tests cover credential filtering, private files, opt-outs, firewall
drift and gateway changes. Physical Wi-Fi range/firmware and the actual new
network still need verification on the car devices.

Developers can run `bash dev/check-car-network.sh` with a local Linux Docker
daemon. It checks real client packets, stacked nftables/iptables DROP chains,
NAT, firewall reload recovery, dnsmasq and a pinned restricted SSH import into
real NetworkManager. All packet tests run inside a disposable container with
private network namespaces, synthetic credentials and no external network or
published ports. Building the Ubuntu dependency image requires Internet/disk.
