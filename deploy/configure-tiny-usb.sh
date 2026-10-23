#!/usr/bin/env bash
# One-time setup on the Ubuntu ThinkCentre after the Duet enumerates as USB Ethernet.
# This is intentionally opt-in: never reconfigure an unknown network interface.
set -euo pipefail
if [[ "${EUID}" == 0 ]]; then echo 'Run as ordinary Ubuntu user (sudo requested)' >&2; exit 1; fi
if [[ "${1:-}" == "" ]]; then
  echo "Usage: bash deploy/configure-tiny-usb.sh USB_ETH_INTERFACE" >&2
  echo "Find it with: ip -br link  (connect the Duet USB data cable first)" >&2
  exit 2
fi
IFACE="$1"
[[ "$IFACE" =~ ^[a-zA-Z0-9_.:-]+$ ]] || { echo "Invalid interface name" >&2; exit 1; }
[[ -d "/sys/class/net/$IFACE" ]] || { echo "Interface not found: $IFACE" >&2; exit 1; }
case "$IFACE" in lo|eth0|enp*|eno*|wlan*|wlp*) 
  # enp names are possible for USB on some devices; driver test below decides.
  ;;
esac
DEVICE_PATH="$(readlink -f "/sys/class/net/$IFACE/device" || true)"
DRIVER="$(basename "$(readlink -f "/sys/class/net/$IFACE/device/driver" 2>/dev/null || echo /unknown)")"
if [[ "$DEVICE_PATH" != *"/usb"* ]] ||
   [[ ! "$DRIVER" =~ ^(cdc_ether|cdc_ncm|rndis_host|usbnet|cdc_mbim)$ ]]; then
  echo "Refusing to change '$IFACE': not recognized as a USB gadget Ethernet adapter (driver $DRIVER)." >&2
  exit 1
fi
command -v nmcli >/dev/null || { echo 'NetworkManager (nmcli) is required' >&2; exit 1; }
echo "Detected USB Ethernet: $IFACE (driver: $DRIVER)"
echo "Assign Tiny address 172.16.42.2/24, no gateway, without modifying Wi-Fi or other Ethernet."
echo "The Duet's usual postmarketOS gadget address is 172.16.42.1."
read -r -p "Type yes to configure this USB interface: " answer
[[ "$answer" == yes ]] || exit 0
if nmcli -g GENERAL.STATE device show "$IFACE" >/dev/null 2>&1; then :; fi
if nmcli -t -f NAME con show | grep -Fxq drive-duet-usb; then
  echo "Profile drive-duet-usb already exists; refusing to overwrite it." >&2
  exit 1
fi
sudo nmcli con add type ethernet con-name drive-duet-usb ifname "$IFACE" \
  connection.autoconnect yes ipv4.method manual ipv4.addresses 172.16.42.2/24 \
  ipv4.never-default yes ipv6.method disabled
sudo nmcli con up drive-duet-usb
echo
echo 'USB link configured. Check: ping -c 2 172.16.42.1'
echo 'The Tiny also needs an SSH server listening on this interface.'
echo 'If necessary: sudo apt install openssh-server && sudo systemctl enable --now ssh'
echo 'Keep /etc/car-dashboard/dashboard.env CAR_BIND=127.0.0.1 (do not expose port 8787).'
