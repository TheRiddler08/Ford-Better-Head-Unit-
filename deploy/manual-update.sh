#!/usr/bin/env bash
# Run as the Ubuntu login user. Does not enable automatic checks.
set -euo pipefail
[[ "$EUID" != 0 ]] || { echo 'Run as your normal user, not root' >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ -d "$ROOT/.git" ]] || { echo 'A git clone is required' >&2; exit 1; }
BRANCH="$(git -C "$ROOT" symbolic-ref --quiet --short HEAD)"
[[ "$BRANCH" == main ]] || { echo "Switch to main before updating (currently $BRANCH)" >&2; exit 1; }
sudo env REPO="$ROOT" APP_USER="$(id -un)" BRANCH="$BRANCH" bash "$ROOT/deploy/update.sh"
