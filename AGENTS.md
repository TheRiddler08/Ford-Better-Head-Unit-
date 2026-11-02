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

- Resumable small updates must use the shared signed transfer identity and durable
  receipt journal across IP/radio. A broker TX receipt is not installation success.
  Keep actual modem/network provisioning explicitly pending when its details are
  unknown; a generic adapter boundary is not proof of an operational LoRaWAN link.

- Local GPS history remains readable without encryption. All application data sent
  over LoRaWAN must use established public/private-key encryption at the actual
  radio boundary, with no plaintext downgrade; preserve existing authentication.
- Shared car archives use an existing Linux distributed filesystem (MooseFS), not
  a custom storage engine. Keep active SQLite/WAL files local, retain originals,
  and never write archive data into an unmounted pool directory.
- The camera is a Botslab G980H 4 Channel Dash Cam Set. Preserve supplied channel
  folders and original filenames. Do not assume undocumented Wi-Fi/Bluetooth APIs
  or claim video transfer over LoRaWAN when only catalog metadata is implemented.
- The G980H is temporary. A future secondary dashcam will use USB cameras and a
  separate Raspberry Pi 5 with an AI HAT for onboard object detection. This is
  notes-only until requested; see docs/FUTURE_USB_CAMERA_AI.md.
- Future AI may also use the home server for more advanced processing over a
  hotspot Internet connection, while local recording/detection continue offline.
  This remains a planning note, not authorization to enable video uploads now.
