# Boot directly to the dashboard

The normal installer (`bash deploy/install.sh`) now configures a full-screen local browser dashboard at power-on on Ubuntu Desktop using GDM3. This is intended for the Lenovo M910q Tiny in the car.

## What it does

1. The dashboard API starts through the existing `car-dashboard.service` at boot.
2. GDM3 signs the installing desktop user in automatically (no graphical login prompt).
3. GNOME launches `deploy/kiosk-launch.sh` using `~/.config/autostart/car-dashboard-kiosk.desktop`.
4. The launcher waits for the local dashboard API at `http://127.0.0.1:8787/api/health`, then launches an installed Chromium, Chrome or Firefox browser fullscreen.
5. If the browser closes or crashes, the launcher opens it again after three seconds. GNOME idle screen blanking and screen lock are disabled inside this user session.

**The installer does not reboot or restart GDM.** After installation, reboot when you are ready, ideally with the physical touchscreen connected. No internet is required once installed for the browser or dashboard to run.

## Install

```bash
cd ~/car-dashboard
bash deploy/install.sh
```

For an already installed dashboard:

```bash
cd ~/car-dashboard
bash deploy/enable-kiosk.sh
# Reboot when ready to apply automatic login.
```

If you want a normal password-based Ubuntu login instead:

```bash
CAR_KIOSK=0 bash deploy/install.sh
```

Remove boot-to-dashboard and restore prior login configuration:

```bash
cd ~/car-dashboard
bash deploy/disable-kiosk.sh
# Reboot when ready to apply the restored login configuration.
```

The GDM helper edits only automatic-login keys in `/etc/gdm3/custom.conf` and preserves your Wayland/X11 settings. It keeps a backup at `/etc/car-dashboard/gdm3.before-kiosk.conf`. An autostart launcher log is available at `~/.local/state/car-dashboard/kiosk.log`. If GDM3 is absent, kiosk setup stops with an error rather than changing a different display manager.

## Security and hardware

**A no-login desktop is not a secure kiosk.** The desktop account (usually `cayden`) is logged in at power-on. Anyone with physical keyboard/mouse access may be able to leave the fullscreen browser and read that account's files. The installer does not configure a dedicated restricted kiosk account. For unattended permanent deployments, that is a recommended later hardening step.

The computer still requires a working physical or virtual X11/Wayland display; no software can make an unrecognized physical screen appear automatically.

**If you configured the earlier software-only Xorg display:** it may override the actual DisplayPort touchscreen once connected. Only after physically connecting the touchscreen and confirming you can access MeshCentral Terminal or SSH, disable the legacy dummy-display file:

```bash
sudo mv /etc/X11/xorg.conf.d/90-car-headless.conf /etc/X11/xorg.conf.d/90-car-headless.conf.disabled
sudo reboot
```

This is a one-time switch from the remote-testing virtual monitor to the real hardware display. Do **not** make the change while completely headless unless you have reliable recovery access. If the real touchscreen isn't attached and remote Desktop fails, MeshCentral Terminal/SSH remain usable. No vehicle control, OBD2 serial interaction, or recorder modifications are made by this kiosk feature.

Auto-updates: GitHub updates will pull future changes to the kiosk launcher automatically, but will **not** modify the GDM automatic-login configuration without explicitly running the kiosk enable/disable command. An installation performed before kiosk support was added requires `bash deploy/enable-kiosk.sh` once.
