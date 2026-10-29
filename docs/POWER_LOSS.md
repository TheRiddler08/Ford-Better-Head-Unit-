# Tiny power loss and shutdown

The Tiny currently loses its supply when the car turns off. Software alone cannot
make that safe: once power disappears it has no time to stop Home Assistant,
finish an update, flush an SSD cache or shut Linux down. Park is not ignition-off;
a stationary speed reading, stale GPS fix or missing OBD response is not proof of
Park. This repository currently has no verified gear/ignition input and never
powers the Tiny off based on those guesses.

## Protection available now

- The managed OBD and GPS recorders explicitly use SQLite WAL with
  `synchronous=FULL` on every connection. Each existing per-record transaction is
  committed with SQLite's durability flushes. This applies while moving as well as
  while parked, so there is no dependency on detecting Park in time.
- SQLite can recover committed WAL transactions after interruption. Keep the
  database, `-wal` and `-shm` files together: never delete a WAL to fix a database.
- A systemd timer runs an unprivileged `/usr/bin/sync` every 15 seconds to reduce
  dirty filesystem data, including Home Assistant's already-written files. It
  does not stop new writes, flush application memory, establish a safe-cut window,
  or replace a UPS. Failures/timeouts appear in the service journal.
- Generated Home Assistant configuration commits recorder batches every second.
  Existing user-edited configuration is preserved; if you maintain your own
  `recorder:` section, you can set `commit_interval: 1` there yourself. This does
  not guarantee every HA event is on persistent storage.

These settings trade additional disk writes/possible recording latency for
reduced data loss. Durability still depends on drive flush behavior, filesystem,
storage health and power-loss protection. An interrupted OS/package/Git update
can still need repair. A periodic sync does not make such updates power-cut safe.

## Install on the existing Tiny

Once this commit is in its checkout (via the Duet courier or a normal update), run
as the normal dashboard user on **Tiny**:

```sh
cd ~/car-dashboard
bash deploy/install-power-safety.sh
sudo systemctl restart car-obd-monitor.service
```

If the managed GPS recorder is installed, also run:

```sh
sudo systemctl restart car-gps-recorder.service
```

Restart only the managed repository recorders: a custom/older recorder service
needs migration before these Python database settings will apply. Full new Tiny
installation includes the timer, and full courier receivers containing this
version enable it during runtime installation. Older installed receivers need
this one-time command or their bootstrap upgrade; a code-only Git delivery does
not install system services.

Check it with:

```sh
systemctl list-timers car-disk-flush.timer --no-pager
sudo journalctl -u car-disk-flush.service -n 20 --no-pager
```

The timer keeps running without a desktop login or user D-Bus session. No vehicle
commands, CAN writes, automatic poweroff, filesystem mounts or BIOS settings are
changed by this installer.

## Reliable solution: retained power plus an ignition signal

Use an automotive-rated power controller/UPS sized for the Tiny and its peripherals,
with a genuine ignition-off notification and enough hold-up time for the measured
worst-case shutdown. An ordinary instantly switched adapter cannot provide this.
The controller should request an orderly Linux poweroff, retain power until the
Tiny finishes shutdown, and only then disconnect the supply. Its low-battery
behavior and ignition returning during shutdown also need testing. A qualified
installer should handle fused automotive wiring and voltage/transient protection.

With retained power, the intended sequence is:

1. Verified Park can request an extra flush, without shutting the dashboard down.
2. The controller's ignition-off/UPS event requests normal Linux shutdown.
3. systemd stops HA/Docker and recorders; Linux flushes and unmounts storage.
4. The controller cuts supply after shutdown completes.

The hardware-specific event integration is not configured until the controller
model/interface and reliable gear source are known. Do not wire vehicle voltage
directly to a computer GPIO/USB pin. For a manually supervised test while power
is retained, `sudo systemctl poweroff` requests normal shutdown. Wait for actual
shutdown completion before removing power. Enable the Tiny firmware's restore-on-
AC-power option if you want it to boot when supply returns; verify that behavior
on the actual Tiny.

Cloud tests verify both recorder databases retain committed rows and reject an
unfinished transaction after a writer is killed with SIGKILL. Those tests check
process-crash recovery, not electrical power-loss guarantees. Validate actual
shutdown timing, storage recovery and restart on your devices using backups and
a test database, rather than risking the only copy of vehicle history.
