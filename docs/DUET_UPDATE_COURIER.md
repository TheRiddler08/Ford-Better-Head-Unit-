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

Pairing uses administrative password/keyboard-interactive authentication by
default, excluding agent keys and existing multiplexed connections. The
display-only key can authenticate but is restricted from running pairing
commands, so it must never be selected for this bootstrap. The courier key
remains restricted too. Host-key verification is preserved.

For a Tiny that permits only an administrative SSH key, explicitly select your
normal unrestricted admin identity when running setup:

```sh
CAR_ADMIN_IDENTITY="$HOME/.ssh/my-admin-key" bash duet/setup-offline-updates.sh cayden@172.16.42.2
```

Do not select the display or courier identity. SSH and sudo may prompt separately
for the Tiny's normal password. Failed bootstrap phases now report their names;
background downloads are not cancelled by a pairing failure.
