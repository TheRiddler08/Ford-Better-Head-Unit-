#!/usr/bin/env bash
# Read-only audit for Duet power states, wake routes, battery and Ethernet.
# IMPORTANT: never writes sysfs, changes firmware, or suspends the machine.
set -u

section(){ printf '\n=== %s ===\n' "$1"; }
show_file() {
  local path="$1"
  if [[ -r "$path" ]]; then
    printf '%s: ' "$path"
    cat "$path" 2>/dev/null || printf '(unreadable)\n'
  else
    printf '%s: unavailable\n' "$path"
  fi
}
show_cmd(){
  printf '$'
  printf ' %q' "$@"
  printf '\n'
  "$@" 2>&1 || printf '(command unavailable or failed)\n'
}

section "Board and kernel"
show_file /proc/device-tree/model
printf '\n'
show_cmd uname -r

section "Suspend support (presence does not mean reliable resume)"
show_file /sys/power/state
show_file /sys/power/mem_sleep
show_file /sys/power/disk
if command -v systemctl >/dev/null 2>&1; then
  show_cmd systemctl is-active systemd-logind.service
fi

section "Battery and external power"
shopt -s nullglob
for supply in /sys/class/power_supply/*; do
  printf '\n%s\n' "${supply##*/}"
  for field in type status capacity online voltage_now current_now power_now; do
    [[ -f "$supply/$field" ]] && show_file "$supply/$field"
  done
done

section "USB Ethernet wake capability"
iface="${1:-eth0}"
if [[ ! "$iface" =~ ^[a-zA-Z0-9_.:-]+$ || ! -d "/sys/class/net/$iface" ]]; then
  printf 'Interface %s missing/invalid. Supply its name as the first argument.\n' "$iface"
  exit 1
fi
show_file "/sys/class/net/$iface/address"
show_file "/sys/class/net/$iface/operstate"
if command -v ethtool >/dev/null 2>&1; then
  show_cmd ethtool "$iface"
else
  printf 'ethtool missing. On postmarketOS/Alpine: sudo apk add ethtool\n'
fi

interface_path="$(readlink -f "/sys/class/net/$iface/device" 2>/dev/null || true)"
if [[ -n "$interface_path" ]]; then
  printf 'Driver device: %s\n' "$interface_path"
  show_file "$interface_path/power/wakeup"
  # USB function e.g. 2-1.3.1:1.0 has parent USB device 2-1.3.1.
  usb_parent="$(dirname "$interface_path")"
  show_file "$usb_parent/power/wakeup"
  show_file "$usb_parent/power/control"
else
  printf 'Could not locate USB driver sysfs path\n'
fi
printf '\nAn ethtool Wake-on line or enabled USB wakeup is NOT proof that the\n'
printf 'Duet firmware will resume from suspend when the Tiny sends a packet.\n'
printf 'Do not enable automatic suspend until the wake test succeeds repeatedly.\n'
