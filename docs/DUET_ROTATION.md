# Wormdingler Duet 3 automatic rotation offset

The Chromebook Duet 3 (Google Wormdingler) running GNOME/postmarketOS can
interpret its accelerometer relative to a panel rotated by 90 degrees,
especially evident after detaching its keyboard.

The opt-in helper `duet/fix-rotation.sh` installs one udev mounting matrix
rule for the Chromebook's `platform:cros-ec-accel` sensor. It does not force
landscape mode, change GNOME's rotation lock, or alter the Tiny/dashboard.

On **Duet**:

```bash
cd ~/car-dashboard
bash duet/fix-rotation.sh --status
sudo bash duet/fix-rotation.sh --apply
sudo reboot
```

Test keyboard attached and detached, and portrait and landscape rotations.
If the orientation is still wrong, gather `monitor-sensor` output and the
sensor udev properties to confirm which physical accelerometer GNOME uses.

Rollback:

```bash
sudo bash duet/fix-rotation.sh --revert
sudo reboot
```

The helper backs up a pre-existing rule and refuses to delete unknown edits.
If the device is not Wormdingler it refuses to apply.
