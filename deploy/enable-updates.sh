#!/usr/bin/env bash
# Installs a root-owned 15 minute systemd update timer (opt-in).
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as Ubuntu login user, not root' >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
APP_USER="$(id -un)"
for exe in git flock runuser systemctl python3; do command -v "$exe" >/dev/null || { echo "Missing $exe" >&2; exit 1; }; done
[[ -d "$ROOT/.git" ]] || { echo 'Use a git clone first'; exit 1; }
BRANCH="$(git -C "$ROOT" symbolic-ref --quiet --short HEAD)"
REMOTE="$(git -C "$ROOT" remote get-url origin)"
[[ "$REMOTE" == 'https://github.com/TheRiddler08/Ford-Better-Head-Unit-.git' ]] || { echo "Unexpected origin: $REMOTE"; exit 1; }
[[ "$ROOT" != *' '* && "$ROOT" != *'%'* ]] || { echo 'No spaces or % in project directory please'; exit 1; }
[[ -f /etc/systemd/system/car-dashboard.service ]] || { echo 'Run deploy/install.sh first'; exit 1; }
echo "Auto update source: $REMOTE, branch: $BRANCH"
echo 'WARNING: updates will execute new code from this branch. Protect branch permissions.'
read -r -p 'Enable automatic GitHub update checks every 15 minutes? Type yes: ' answer
[[ "$answer" == yes ]] || { echo 'Cancelled'; exit 0; }
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
{
 printf '#!/usr/bin/env bash\nset -euo pipefail\n'
 printf 'REPO=%q\nAPP_USER=%q\nBRANCH=%q\n' "$ROOT" "$APP_USER" "$BRANCH"
 sed '1,3d' "$ROOT/deploy/update.sh"
} > "$TMP"
sudo install -o root -g root -m 0755 "$TMP" /usr/local/sbin/car-dashboard-update
sudo tee /etc/systemd/system/car-dashboard-update.service >/dev/null <<'UNIT'
[Unit]
Description=Auto-update Ford dashboard (tests and rollback)
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/car-dashboard-update
TimeoutStartSec=180
UNIT
sudo tee /etc/systemd/system/car-dashboard-update.timer >/dev/null <<'UNIT'
[Unit]
Description=Check Ford dashboard updates every 15 minutes
[Timer]
OnCalendar=*-*-* *:0/15:00
RandomizedDelaySec=60s
Persistent=true
Unit=car-dashboard-update.service
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now car-dashboard-update.timer
echo 'Enabled. Logs: sudo journalctl -u car-dashboard-update.service -n 60 --no-pager'
