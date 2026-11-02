#!/usr/bin/env bash
# Pi is the automatic preferred Wi-Fi gateway and boot wake coordinator.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
bash "$ROOT/deploy/install-fleet.sh" pi
