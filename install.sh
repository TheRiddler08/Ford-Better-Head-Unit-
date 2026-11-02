#!/usr/bin/env bash
# One ordinary-user entry point. Role installers apply all managed settings.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
case "${1:-}" in
 duet) exec bash "$ROOT/duet/prepare-update.sh" "${2:-cayden@172.16.42.2}" ;;
 tiny) exec bash "$ROOT/deploy/install.sh" ;;
 pi) exec bash "$ROOT/pi/install.sh" ;;
 laptop) exec bash "$ROOT/laptop/install.sh" ;;
 home) exec bash "$ROOT/home_server/install.sh" ;;
 *) echo 'Usage: bash install.sh duet|tiny|pi|laptop|home' >&2; exit 2 ;;
esac
