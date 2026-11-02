# Device setup expectations

- Requested Pi, Tiny and Duet software settings must be implemented in the normal
  role installation and automatic update paths by default, unless the user
  explicitly asks otherwise. A separate optional script or manual post-install
  checklist alone does not finish such a request.
- Keep the entry points in `install.sh`, the existing role installers, and the
  Tiny/Duet signed courier compatible with older installed receivers. Pi is the
  wake coordinator/preferred Wi-Fi gateway; Duet is the display/update carrier
  and fallback Wi-Fi gateway; Tiny hosts HA and vehicle/GPS recorders.
- The central Ethernet switch is the primary LAN. Automate supported discovery,
  sharing and power settings while preserving explicit opt-outs, existing Wi-Fi
  credentials, SSH authentication, Tailscale, user data and edited dashboards.
- Keep source/signature/hash/native package verification. Run immutable verified
  installer versions as root; never substitute editable working-tree execution
  into the automatic privileged update path.
- Report unsupported firmware, ambiguous adapters and missing hardware as pending
  conditions. Do not claim that software can create firmware boot-on-power or
  reliable physical wake support. Keep a device awake when a safe wake path
  cannot be established.
- Test routing/election/migration behavior without changing the cloud host's
  network or power state. Clearly separate automated validation from physical
  device validation.
