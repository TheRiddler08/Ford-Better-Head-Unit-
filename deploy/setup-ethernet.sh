#!/usr/bin/env bash
# Offline-first wired Ethernet provisioning for the car's Duet and Tiny.
# NetworkManager reconnects automatically. A systemd timer discovers a newly
# attached dedicated Ethernet adapter without touching established networks.
set -euo pipefail

usage() {
  echo "Usage: sudo bash deploy/setup-ethernet.sh duet|tiny --install [--interface IFACE]"
  echo "       sudo bash deploy/setup-ethernet.sh duet|tiny --once [--interface IFACE]"
}
ROLE="${1:-}"
if [[ "$ROLE" != duet && "$ROLE" != tiny ]]; then usage; exit 2; fi
shift
MODE=""
EXPLICIT_IFACE=""
while (($#)); do
  case "$1" in
    --install|--once)
      [[ -z "$MODE" ]] || { usage; exit 2; }
      MODE="$1"; shift ;;
    --interface)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      EXPLICIT_IFACE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
[[ -n "$MODE" ]] || { usage; exit 2; }
[[ -z "$EXPLICIT_IFACE" || "$EXPLICIT_IFACE" =~ ^[a-zA-Z0-9_.:-]+$ ]] || {
  echo "Invalid interface name" >&2; exit 2;
}
(( EUID == 0 )) || { echo "Run with sudo; NetworkManager configuration requires root." >&2; exit 1; }
for cmd in nmcli ip readlink; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 1; }
done

if [[ "$ROLE" == duet ]]; then
  PROFILE="car-duet-wired-tablet"
  ADDRESS="172.16.42.1/24"
else
  PROFILE="car-duet-wired-tiny"
  ADDRESS="172.16.42.2/24"
fi

# A dedicated adapter must not already provide networking or a default route.
is_available() {
  local nic="$1" type="" device_path=""
  [[ "$nic" != lo && "$nic" != tailscale* && "$nic" != wlan* && "$nic" != wlp* ]] || return 1
  [[ -d "/sys/class/net/$nic" ]] || return 1
  type="$(nmcli -g GENERAL.TYPE device show "$nic" 2>/dev/null || true)"
  [[ "$type" == ethernet ]] || return 1
  if [[ "$ROLE" == duet ]]; then
    # Only USB Ethernet adapters, never Wi-Fi or a virtual gadget.
    device_path="$(readlink -f "/sys/class/net/$nic/device" 2>/dev/null || true)"
    [[ "$device_path" =~ /usb[0-9]+/ ]] || return 1
  fi
  [[ -z "$(ip -4 route show default dev "$nic" 2>/dev/null)" ]] || return 1
  [[ -z "$(ip -6 route show default dev "$nic" 2>/dev/null)" ]] || return 1
  # Do not steal an existing addressed connection, even when explicitly named.
  [[ -z "$(ip -o -4 addr show dev "$nic" scope global 2>/dev/null)" ]] || return 1
  [[ -z "$(ip -o -6 addr show dev "$nic" scope global 2>/dev/null)" ]] || return 1
  # Refuse any already established NM connection even if it has no address.
  local state
  state="$(nmcli -g GENERAL.STATE device show "$nic" 2>/dev/null || true)"
  [[ "$state" != 100\ \(connected\) && "$state" != 100* ]] || return 1
  return 0
}

install_timer() {
  local script="/usr/local/libexec/car-duet-ethernet" unit="car-duet-ethernet-$ROLE"
  command -v systemctl >/dev/null || { echo "systemd is required." >&2; exit 1; }
  install -d -m 0755 /usr/local/libexec
  install -m 0755 "$0" "$script"
  local args="$ROLE --once"
  [[ -z "$EXPLICIT_IFACE" ]] || args="$args --interface $EXPLICIT_IFACE"
  cat > "/etc/systemd/system/$unit.service" <<EOF
[Unit]
Description=Safely configure dedicated car Ethernet ($ROLE)
Wants=NetworkManager.service
After=NetworkManager.service
[Service]
Type=oneshot
ExecStart=$script $args
EOF
  cat > "/etc/systemd/system/$unit.timer" <<EOF
[Unit]
Description=Discover dedicated car Ethernet ($ROLE)
[Timer]
OnBootSec=15s
OnUnitInactiveSec=30s
Unit=$unit.service
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$unit.timer"
  echo "Installed $unit.timer: will find a safe free Ethernet interface after adapter is plugged in."
}

if [[ "$MODE" == --install ]]; then
  install_timer
fi

# Existing profiles must be checked for activation, not merely existence:
# the peer going offline can leave an Ethernet device disconnected. Resolve the
# UUID explicitly, to avoid activating an ambiguous/duplicated connection.
PROFILE_UUIDS=()
while IFS=: read -r conn_name conn_uuid unused; do
  if [[ "$conn_name" == "$PROFILE" && -n "$conn_uuid" ]]; then
    PROFILE_UUIDS+=("$conn_uuid")
  fi
done < <(nmcli -t -f NAME,UUID connection show)
if (( ${#PROFILE_UUIDS[@]} > 1 )); then
  echo "WARNING: Multiple '$PROFILE' profiles exist. Refusing ambiguous activation; resolve by UUID." >&2
  exit 1
fi
if (( ${#PROFILE_UUIDS[@]} == 1 )); then
  uuid="${PROFILE_UUIDS[0]}"
  nic="$(nmcli -g connection.interface-name connection show uuid "$uuid" 2>/dev/null || true)"
  if [[ -z "$nic" || "$nic" == "--" ]]; then
    echo "Existing '$PROFILE' has no pinned interface; refusing to guess a NIC." >&2
    exit 1
  fi
  if [[ -n "$EXPLICIT_IFACE" && "$EXPLICIT_IFACE" != "$nic" ]]; then
    echo "Profile '$PROFILE' is bound to $nic, not requested $EXPLICIT_IFACE. No changes." >&2
    exit 1
  fi
  if [[ ! -d "/sys/class/net/$nic" ]]; then
    echo "Waiting for $nic to reappear; leaving profile and other networks untouched."
    exit 0
  fi
  if [[ "$(nmcli -g GENERAL.TYPE device show "$nic" 2>/dev/null || true)" != ethernet ]]; then
    echo "Expected Ethernet at $nic; refusing activation." >&2
    exit 1
  fi
  if [[ "$ROLE" == duet ]]; then
    nic_path="$(readlink -f "/sys/class/net/$nic/device" 2>/dev/null || true)"
    if [[ ! "$nic_path" =~ /usb[0-9]+/ ]]; then
      echo "Refusing non-USB Duet device $nic." >&2
      exit 1
    fi
  fi
  # Never bring up a similarly named, manually modified profile with a
  # different IP. That could otherwise steal a remote-management interface.
  if [[ "$(nmcli -g ipv4.method connection show uuid "$uuid" 2>/dev/null || true)" != manual ||
        "$(nmcli -g ipv4.addresses connection show uuid "$uuid" 2>/dev/null || true)" != "$ADDRESS" ]]; then
    echo "Existing '$PROFILE' does not have expected IPv4 $ADDRESS; refusing activation." >&2
    exit 1
  fi
  active="$(nmcli -g GENERAL.CONNECTION device show "$nic" 2>/dev/null || true)"
  if [[ "$active" == "$PROFILE" ]]; then
    echo "Already connected: $nic -> $PROFILE."
    exit 0
  fi
  if [[ -n "$active" && "$active" != "--" ]]; then
    echo "Device $nic already uses '$active'. Not replacing another connection."
    exit 0
  fi
  # During a Tiny reboot, link carrier is usually absent. A failed nmcli up
  # must not block later retries or trigger profile recreation.
  if [[ ! -r "/sys/class/net/$nic/carrier" ||
        "$(cat "/sys/class/net/$nic/carrier" 2>/dev/null || true)" != 1 ]]; then
    echo "Ethernet link $nic has no carrier yet; retrying on the next timer run."
    exit 0
  fi
  echo "Restoring existing wired connection '$PROFILE' on $nic using UUID $uuid."
  if ! nmcli --wait 12 connection up uuid "$uuid" ifname "$nic"; then
    echo "Activation unsuccessful; will retry, without changing other networks." >&2
    exit 1
  fi
  exit 0
fi

CANDIDATES=()
if [[ -n "$EXPLICIT_IFACE" ]]; then
  if is_available "$EXPLICIT_IFACE"; then
    CANDIDATES+=("$EXPLICIT_IFACE")
  else
    echo "Refusing $EXPLICIT_IFACE: absent, not a dedicated Ethernet adapter, or already in use." >&2
    exit 1
  fi
else
  while IFS=: read -r nic kind rest; do
    [[ "$kind" == ethernet ]] || continue
    if is_available "$nic"; then CANDIDATES+=("$nic"); fi
  done < <(nmcli -t -f DEVICE,TYPE device status)
fi
if ((${#CANDIDATES[@]} == 0)); then
  echo "No free dedicated Ethernet adapter found. Existing networks were not changed."
  exit 0
fi
if ((${#CANDIDATES[@]} != 1)); then
  echo "Multiple free Ethernet interfaces found: ${CANDIDATES[*]}" >&2
  echo "Specify one with --interface; no network settings were changed." >&2
  exit 1
fi

NIC="${CANDIDATES[0]}"
echo "Configuring $ROLE Ethernet $NIC at $ADDRESS (no gateway or DNS)."
nmcli connection add type ethernet con-name "$PROFILE" ifname "$NIC" \
  connection.autoconnect yes connection.autoconnect-priority 100 \
  connection.autoconnect-retries 0 \
  ipv4.method manual ipv4.addresses "$ADDRESS" ipv4.never-default yes \
  ipv4.ignore-auto-dns yes ipv6.method disabled \
  802-3-ethernet.cloned-mac-address permanent
# No cable carrier is normal during first setup. The profile remains persistent.
if [[ -r "/sys/class/net/$NIC/carrier" ]] && [[ "$(cat "/sys/class/net/$NIC/carrier")" == 1 ]]; then
  nmcli connection up "$PROFILE" || echo "Cable present but activation pending; NetworkManager will retry." >&2
fi
echo "NetworkManager will reconnect $PROFILE whenever this adapter has a link."
