# Security and safety

- **Keep the service on localhost.** There is no authentication on the dashboard. Use authenticated SSH port forwarding or a secured Tailscale tunnel if you need off-device access. Do not publish port 8787 to the Internet.
- **Never commit vehicle data or access tokens.** The dashboard excludes SQLite databases and private environment files; the production database remains at `/home/cayden/car-console/obd-data/vehicle.sqlite3`.
- **Treat `main` as a deployment branch.** Opt-in automated updates run new code on the vehicle PC. Require pull-request review / appropriate branch protection; don't grant write access to untrusted contributors.
- **The dashboard is not a vehicle control system.** It reads SQLite only. Do not substitute it for factory instruments, navigation safety, or the vehicle's controls. Fuel-level-zero warnings and trips are estimates, not diagnostics or odometer measurements.
- **Keep a fallback.** The current OBD2 monitor runs independently. The updater restarts only `car-dashboard.service` and rolls back dashboard code if tests or local API checks fail. It does not roll back external migrations or OS changes.

## Automatic login kiosk

The standard installer offers immediate dashboard startup by enabling GDM automatic login for the installing user. This is not a hardened kiosk: anybody with physical access may reach that account outside the fullscreen browser. To disable, run `bash deploy/disable-kiosk.sh` as that user and reboot. Consider using a dedicated restricted desktop account for an unattended vehicle.

## Custom display projects

Home-authored HTML/CSS/JavaScript runs in an opaque-origin iframe with scripts enabled and a separate restrictive CSP. The parent sends read-only telemetry; custom content cannot access parent DOM, API credentials, filesystem or vehicle commands. External network access and nested frames are blocked. The authenticated panel retains its own CSP. Publish only scripts you have previewed; browser sandboxing does not bound CPU or memory consumption. The standard dashboard and recovery link remain outside authored content. Layout delivery acknowledgments confirm saving, not successful visual rendering.

## GPS archives and carried updates

GPS history stays local and has no automatic deletion; protect it and its backups.
The update courier packages repository source only, never vehicle/location databases.
It uses a separate pinned Ed25519 key and strict SSH host checking. Its restricted
Tiny receiver verifies signed metadata and bundle hashes, permits only fast-forward
`main` updates from the configured repository, preserves local work, runs tests as
an ordinary user, and controls only fixed dashboard/recorder services. The key grants
code-deployment authority; do not reuse the display key or copy the courier private
key to the Tiny. The legacy Git-only receiver is a fixed root-owned bootstrap. The full v2 courier
explicitly authorizes signed, versioned managed-bootstrap updates as described below.

## Car Home Assistant

Home Assistant binds to Tiny loopback and uses a separate restricted Duet SSH forward. Owner authentication stays enabled; installation never writes auth storage or invents an owner password. The integration polls the existing read-only backend and has no serial/device or Docker-socket mount. Its official container image is digest-pinned. Managed updates preserve edited config and native storage, validate before restarting, and roll back rejected assets. Repository write/deployment access still authorizes executable integration code; protect it accordingly.

## Full offline runtime courier

The full courier key also authorizes privileged managed installer updates. Ubuntu Release/index/package signatures and hashes remain enforced through native APT with trusted archive keys and temporary local-only sources. Official HA image manifests, compressed layer hashes, unpacked diff IDs, archive hashes and loaded image IDs are checked. Managed bootstrap versions are published atomically from the signed immutable Git commit. HA upgrades back up private configuration/database state and require a healthy live car integration; failed updates remain queued with backoff. Protect the signing key and repository permissions as code-deployment authority.
