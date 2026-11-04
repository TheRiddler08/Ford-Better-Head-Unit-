# Pi 4 with an 8 GB microSD card

The Pi role defaults to a shared-storage **client**, not a contributor. Tiny and
Duet hold bulk archives through MooseFS. Pi does not run Car Assistant/Docker,
import dashcam videos, contribute chunks, or retain MooseFS metadata backups.
It no longer needs 8 GiB free just to mount the pool. Fresh setup installs the
MooseFS client without storage servers or FFmpeg.

This is not diskless boot. Linux, installed software, the Git checkout (including
versioned project assets), credentials and small recovery journals remain on SD.
Active SQLite/WAL databases stay local; radio/update receipts must survive power
cuts.

## Update

On Pi, as its normal user:

```sh
cd ~/car-dashboard
git pull --ff-only
bash install.sh pi
python3 /usr/local/lib/car-dashboard/fleet-current/fleet.py status
```

Automatic Pi role updates apply the same defaults. Status reports storage mode
`client-only` and `contributionBytes: 0`. Mount retries do not block boot when
Tiny is disconnected; sharing and wake coordination continue independently.

## Storage behavior

- New archive objects go directly to `/car-data/pi/objects`, without an SD bulk
  spool. Open Linux directory descriptors pin the MooseFS filesystem. Unmounts,
  replacement mount paths and directory symlinks cannot redirect writes onto SD.
- Tiny remains the pool master. Duet contributes space but cannot replace an
  offline Tiny master. Bulk writes wait when the pool is unavailable; source
  files are retained.
- Managed Pi workers cap each new SQLite state database at 16 MiB and tune WAL
  checkpoint/journal retention. Local atomic state files are limited to 32 MiB
  each, without imposing that file-size restriction on remote objects. Full
  databases apply errors/backpressure rather than erase committed receipts.
  These are per-file bounds, not a quota on the entire OS/home directory.
- Systemd Pi OS stores new journal logs in bounded RAM (32 MiB); they do not
  survive power loss. Fleet heartbeat status uses `/run`; only changed, stable
  discovered MAC identities are checkpointed to SD.
- APT download caches are cleaned and not retained. Existing packages, old logs,
  old chunk directories and archives are not deleted. Existing larger databases
  are preserved rather than shrunk.

Old Pi archive objects can be copied to the pool, preserving originals. Do not
delete old MooseFS chunks without checking the cluster's replica health.

Set `pi_storage_client: false` in `/etc/car-dashboard/fleet.json` and reapply setup
to restore contribution/ingestion on a larger card. Existing journald/apt drop-ins
remain unless deliberately overridden or removed.

## Validation

Tests cover offline refusal, remote writes, pinned directories after mount-path
replacement, symlink rejection, replication-tool descriptor inheritance, package
selection, scoped limits and capacity failures preserving committed state. Actual
cluster availability/free space must still be checked on the devices.
