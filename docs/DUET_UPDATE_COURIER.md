# Duet update courier

Use the [one-device automatic update setup](ONE_DEVICE_UPDATES.md). The current
courier downloads software, the official Home Assistant image and signed Ubuntu
Docker prerequisites on **Duet**, then installs on the offline **Tiny** over the
existing wired SSH link. Home Assistant does not need a separate Internet
connection on Tiny for the carried installation.

The one-time setup, initiated on Duet, is:

```sh
cd ~/car-dashboard
git pull --ff-only
bash duet/setup-offline-updates.sh cayden@172.16.42.2
```

Tiny must be attached for pairing and already have its existing dashboard/SSH
setup. Replace the account as appropriate. After pairing, downloads, retries,
transfers, validation and managed updater self-upgrades run automatically.

Old Git-only v1 receivers require this one-time bootstrap upgrade before accepting
full runtime bundles. All previously recorded car/GPS data and normal recovery
interfaces remain separate from the transferred packages.
