#!/usr/bin/env bash
# Duet 3 (Wormdingler) 90-degree accelerometer auto-rotation correction.
# Reversible, opt-in. Nothing is installed on the Tiny.
set -euo pipefail

RULE_PATH=/etc/udev/rules.d/61-cros-ec-accel.rules
BACKUP_PATH=/etc/udev/rules.d/61-cros-ec-accel.rules.car-original
RULE='ACTION!="remove", SUBSYSTEM=="iio", KERNEL=="iio*", SUBSYSTEMS=="platform", ATTRS{modalias}=="platform:cros-ec-accel", ENV{ACCEL_MOUNT_MATRIX}="0, 1, 0; -1, 0, 0; 0, 0, -1"'
MODE="${1:---status}"

usage() {
  echo "Usage: bash duet/fix-rotation.sh --status"
  echo "       sudo bash duet/fix-rotation.sh --apply"
  echo "       sudo bash duet/fix-rotation.sh --revert"
}

is_managed() {
  [[ -f "$RULE_PATH" ]] && [[ "$(cat "$RULE_PATH")" == "$RULE" ]]
}

reload_rules() {
  udevadm control --reload-rules
  udevadm trigger --subsystem-match=iio --action=change
  echo "Reboot or log out/in if GNOME continues using cached orientation."
}

case "$MODE" in
  --status)
    if [[ -r /proc/device-tree/model ]]; then
      printf 'Board: '
      tr -d '\0' < /proc/device-tree/model
      echo
    fi
    if is_managed; then
      echo "Wormdingler 90-degree fix is installed."
    elif [[ -e "$RULE_PATH" ]]; then
      echo "A custom sensor rule exists at $RULE_PATH (not managed by this helper)."
    else
      echo "Wormdingler 90-degree fix not installed."
    fi
    echo "Rule path: $RULE_PATH"
    ;;
  --apply)
    (( EUID == 0 )) || { echo "Run with sudo" >&2; exit 1; }
    [[ -r /proc/device-tree/model ]] || { echo "Not a recognized Wormdingler device" >&2; exit 1; }
    model="$(tr -d '\0' < /proc/device-tree/model)"
    [[ "${model,,}" == *wormdingler* ]] || { echo "Refusing: this is not the Wormdingler Duet 3." >&2; exit 1; }
    command -v udevadm >/dev/null || { echo "udevadm required" >&2; exit 1; }
    install -d -m 0755 /etc/udev/rules.d
    if is_managed; then
      echo "90-degree correction already installed; reloading rules."
    else
      if [[ -e "$RULE_PATH" ]]; then
        if [[ -e "$BACKUP_PATH" ]]; then
          echo "Backup exists already at $BACKUP_PATH; refusing to overwrite a different rule." >&2
          exit 1
        fi
        cp -p -- "$RULE_PATH" "$BACKUP_PATH"
        echo "Original sensor rule backed up to $BACKUP_PATH"
      fi
      tmp="$(mktemp /etc/udev/rules.d/.61-cros-ec-accel.XXXXXXXX)"
      trap 'rm -f -- "${tmp:-}"' EXIT
      printf '%s\n' "$RULE" > "$tmp"
      chmod 0644 "$tmp"
      mv -f -- "$tmp" "$RULE_PATH"
      trap - EXIT
      echo "Installed accelerometer orientation correction."
    fi
    reload_rules
    ;;
  --revert)
    (( EUID == 0 )) || { echo "Run with sudo" >&2; exit 1; }
    command -v udevadm >/dev/null || { echo "udevadm required" >&2; exit 1; }
    if ! is_managed; then
      echo "No managed rule to revert. Will not delete a user's custom rule." >&2
      exit 1
    fi
    if [[ -e "$BACKUP_PATH" ]]; then
      mv -f -- "$BACKUP_PATH" "$RULE_PATH"
      echo "Restored previous sensor rule."
    else
      rm -f -- "$RULE_PATH"
      echo "Removed sensor correction rule."
    fi
    reload_rules
    ;;
  *) usage; exit 2 ;;
esac
