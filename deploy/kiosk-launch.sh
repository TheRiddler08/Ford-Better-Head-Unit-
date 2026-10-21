#!/usr/bin/env bash
# GNOME graphical-session kiosk. It is not started from SSH or systemd.
set -o pipefail
URL="http://127.0.0.1:8787"
mkdir -p "$HOME/.local/state/car-dashboard"
exec >>"$HOME/.local/state/car-dashboard/kiosk.log" 2>&1
echo "Kiosk starting at $(date -Is), DISPLAY=$DISPLAY"
if command -v gsettings >/dev/null 2>&1; then
  gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
  gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
  gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' 2>/dev/null || true
  gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing' 2>/dev/null || true
fi
if command -v xset >/dev/null 2>&1; then
  xset s off 2>/dev/null || true
  xset s noblank 2>/dev/null || true
  xset -dpms 2>/dev/null || true
fi
BROWSER=""
for exe in chromium chromium-browser google-chrome-stable google-chrome firefox; do
  if command -v "$exe" >/dev/null 2>&1; then
    BROWSER="$exe"
    break
  fi
done
if [[ -z "$BROWSER" ]]; then
  echo "No browser found. Install Firefox or Chromium." >&2
  exit 1
fi
until curl --fail --silent --max-time 2 "$URL/api/health" >/dev/null; do sleep 2; done
while :; do
  echo "$(date -Is): launching $BROWSER"
  if [[ "$BROWSER" == "firefox" ]]; then
    mkdir -p "$HOME/.local/share/car-dashboard-kiosk-firefox"
    "$BROWSER" --no-remote --profile "$HOME/.local/share/car-dashboard-kiosk-firefox" --kiosk --private-window "$URL" || true
  else
    "$BROWSER" --kiosk --incognito --no-first-run --no-default-browser-check \
      --disable-session-crashed-bubble --user-data-dir="$HOME/.config/car-dashboard-kiosk-chromium" "$URL" || true
  fi
  sleep 3
done
