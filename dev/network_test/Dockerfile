FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 nftables iptables iproute2 curl dnsmasq-base openssh-server sudo dbus network-manager && rm -rf /var/lib/apt/lists/*
CMD ["sleep", "infinity"]
