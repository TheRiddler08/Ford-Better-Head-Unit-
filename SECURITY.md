# Security and safety

- **Keep the service on localhost.** There is no authentication on the dashboard. Use authenticated SSH port forwarding or a secured Tailscale tunnel if you need off-device access. Do not publish port 8787 to the Internet.
- **Never commit vehicle data or access tokens.** The dashboard excludes SQLite databases and private environment files; the production database remains at `/home/cayden/car-console/obd-data/vehicle.sqlite3`.
- **Treat `main` as a deployment branch.** Opt-in automated updates run new code on the vehicle PC. Require pull-request review / appropriate branch protection; don't grant write access to untrusted contributors.
- **The dashboard is not a vehicle control system.** It reads SQLite only. Do not substitute it for factory instruments, navigation safety, or the vehicle's controls. Fuel-level-zero warnings and trips are estimates, not diagnostics or odometer measurements.
- **Keep a fallback.** The current OBD2 monitor runs independently. The updater restarts only `car-dashboard.service` and rolls back dashboard code if tests or local API checks fail. It does not roll back external migrations or OS changes.

## Automatic login kiosk

The standard installer offers immediate dashboard startup by enabling GDM automatic login for the installing user. This is not a hardened kiosk: anybody with physical access may reach that account outside the fullscreen browser. To disable, run `bash deploy/disable-kiosk.sh` as that user and reboot. Consider using a dedicated restricted desktop account for an unattended vehicle.
