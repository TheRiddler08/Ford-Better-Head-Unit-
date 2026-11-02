#!/usr/bin/env bash
# Spare laptop: car LAN, shared archives, managed updates and car-off sleep.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
bash "$ROOT/deploy/install-fleet.sh" laptop
