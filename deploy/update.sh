#!/usr/bin/env bash
# This file becomes /usr/local/sbin/car-dashboard-update with REPO, APP_USER, BRANCH prepended.
set -euo pipefail
: "${REPO:?missing REPO}" "${APP_USER:?missing APP_USER}" "${BRANCH:?missing BRANCH}"
exec 9>/run/lock/car-dashboard-update.lock
flock -n 9 || { echo 'Already updating'; exit 0; }
run_git(){ runuser -u "$APP_USER" -- git -C "$REPO" "$@"; }
run_python(){ runuser -u "$APP_USER" -- /usr/bin/python3 "$@"; }
[[ "$(run_git symbolic-ref --short HEAD)" == "$BRANCH" ]] || { echo 'Unexpected branch'; exit 1; }
[[ -z "$(run_git status --porcelain --untracked-files=no)" ]] || { echo 'Local changes; no update'; exit 0; }
[[ "$(run_git remote get-url origin)" == 'https://github.com/TheRiddler08/Ford-Better-Head-Unit-.git' ]] || { echo 'Origin changed; refusing update'; exit 1; }
OLD="$(run_git rev-parse HEAD)"
run_git fetch --no-tags --quiet origin "$BRANCH"
NEW="$(run_git rev-parse FETCH_HEAD)"
[[ "$NEW" != "$OLD" ]] || { echo "Already current at ${OLD:0:12}"; exit 0; }
run_git merge-base --is-ancestor "$OLD" "$NEW" || { echo 'Refusing rewritten branch'; exit 1; }
rollback(){
 echo "Rolling back dashboard to ${OLD:0:12}" >&2
 run_git reset --hard "$OLD"
 systemctl restart car-dashboard.service || true
 if [[ -f /etc/systemd/system/car-obd-monitor.service ]] && grep -Fq "$REPO/recorder/obd_full_recorder.py" /etc/systemd/system/car-obd-monitor.service; then
   systemctl restart car-obd-monitor.service || true
 fi
}
trap rollback ERR
run_git merge --ff-only "$NEW"
run_python -m unittest discover -s "$REPO/tests" -v
run_python -m py_compile "$REPO/app/server.py"
run_python -m py_compile "$REPO/recorder/obd_full_recorder.py"
if command -v node >/dev/null; then node --check "$REPO/app/web/app.js"; fi
systemctl restart car-dashboard.service
healthy=0
for i in {1..15}; do
 if run_python "$REPO/deploy/check-health.py" >/dev/null 2>&1; then healthy=1; break; fi
 sleep 1
done
[[ $healthy == 1 ]] || { echo 'Dashboard failed post-update health check' >&2; exit 1; }
# Restart recorder only when adopted from this repo AND recorder source changed.
# FORScan or manual stops stay respected: do not start an intentionally stopped service.
if [[ -f /etc/systemd/system/car-obd-monitor.service ]] &&
   grep -Fq "$REPO/recorder/obd_full_recorder.py" /etc/systemd/system/car-obd-monitor.service &&
   run_git diff --name-only "$OLD" "$NEW" -- recorder/ | grep -q .; then
   if systemctl is-active --quiet car-obd-monitor.service; then
     systemctl restart car-obd-monitor.service
     systemctl is-active --quiet car-obd-monitor.service
   else
     echo 'Recorder is stopped; leaving it stopped (possibly FORScan is using the port)'
   fi
fi
trap - ERR
echo "Updated ${OLD:0:12} -> ${NEW:0:12}"
