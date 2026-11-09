#!/usr/bin/env bash
# Actual Linux networking/authentication, confined to disposable namespaces.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
IMAGE=car-dashboard-network-validation:local
docker build -t "$IMAGE" "$ROOT/dev/network_test"
# No host network, published ports, writable repo mount or real credentials.
# SYS_ADMIN is needed only for unshare/nsenter inside the disposable container.
docker run --rm --network none --cap-add SYS_ADMIN --cap-add NET_ADMIN \
 --sysctl net.ipv4.ip_forward=1 --sysctl net.ipv4.conf.default.forwarding=1 \
 -e CAR_NETWORK_TEST=1 \
 --mount "type=bind,src=$ROOT,dst=/app,readonly" \
 --mount "type=bind,src=$ROOT/dev/network_test,dst=/fixture,readonly" \
 "$IMAGE" python3 /fixture/check.py
