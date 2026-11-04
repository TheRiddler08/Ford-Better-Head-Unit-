# Shared car storage, encrypted radio data and dashcam recordings

Normal role installation and verified role updates configure these features by
default. No existing databases are moved, disks reformatted, or user originals
deleted. Local GPS history is readable without a decryption key.

## Existing Linux components

- **MooseFS**: the distributed filesystem, FUSE mount, chunk placement, replication
  and metadata backups. Upstream: https://github.com/moosefs/moosefs
- **libsodium / PyNaCl SealedBox**: Curve25519 public-key encryption using the
  established XSalsa20-Poly1305 sealed-box construction. Upstream:
  https://pynacl.readthedocs.io/en/latest/public/#nacl-public-sealedbox
- **FFmpeg / ffprobe**: validate imported camera media; no invented Botslab API.

The repository implements provisioning, a local spool, transfer framing and UI
integration around those existing components. It does not implement a filesystem
or cryptographic primitive.

## Storage layout and operation

Tiny is the MooseFS master at `172.16.42.2`. Tiny and Duet contribute chunk storage
and mount it at `/car-data`; Duet also runs the metadata backup. Pi defaults to a
mount-only client for its 8 GB card, without chunks or a metalogger. See the
[Pi small-card policy](PI_SMALL_CARD.md). This is **not automatic master failover**.
With Tiny unavailable the shared mount can be unavailable. Recording on a running
computer continues on its own local disk.

Packages are installed automatically on supported APT systems. Already available
binaries can also be configured under OpenRC. A system without packaged MooseFS,
FUSE support, enough disk space, or an identifiable service account reports pending
rather than formatting a disk, installing an unverified binary, or pretending its
local mount directory is a working pool. Alpine package availability is not assumed.
The Duet's offline Tiny package cache now includes the new APT dependencies.

Managed state/configuration:

- `/etc/car-dashboard/storage/`: private cluster configuration and allocation.
- `/var/lib/car-storage/`: chunks, master state or metadata backup.
- `/var/lib/car-dashboard-fleet/storage.json`: provisioning status.
- `~/.local/share/car-data/`: local spool, library database and worker status.
- `/car-data/<role>/objects/`: immutable verified archive objects and sidecars.

Each device contributes 60% of its free space at first setup, capped to leave at
least 8 GiB for its OS and recordings. This allocation is saved, not recalculated
on every update. No partitions or other MooseFS configurations are overwritten.
The cluster binds to car Ethernet addresses. Exports allow the three car role IPs,
map operations to an unprivileged shared identity and disable executable/device
mount use. This assumes the physical car LAN is trusted; it is not tenant isolation
or encrypted LAN storage. Do not port-forward MooseFS to the Internet.

The worker takes consistent hourly online SQLite backups of GPS/OBD databases,
copies changed routes/scheduled routes from `~/car-console/routes` and
`~/car-console/scheduled-routes`, and archives the managed `ui-config.json`.
These remain **local car archives**, not radio uploads. Active SQLite/WAL files
remain on local disks. Dashboard/route working copies also stay usable locally.
This is a shared archive plus local working copies, not one block device spanning
all machines or transparent migration of every application data directory.

The worker sets MooseFS goal **2 copies** before publishing archive files and
verifies copy hashes. A configured goal is not proof both replicas exist while
nodes are missing. Local spools and imported originals are retained; there is no
automatic eviction or deletion. This deliberately uses extra space. Near the
2 GiB spool reserve, ingestion pauses rather than silently dropping data. Monitor
capacity and keep an independent backup: abrupt loss of all car power is still a
risk. New master metadata is seeded only into a completely new directory; missing
metadata in an established cluster requires recovery, never an automatic reset.

Use **Reports & GPS history → Check shared storage** for worker/provisioning state.
A disconnected mount is never substituted with a writable ordinary directory.

## Radio encryption

The actual Pi modem-adapter boundary and home ChirpStack bridge now require
sealed-box ciphertext in both directions. Settings, code fragments, acknowledgments,
GPS samples and camera catalog messages receive this application encryption, in
addition to native LoRaWAN encryption. The existing HMAC and signed update checks
remain responsible for sender authorization and update integrity; sealed-box
recipient encryption alone does not authenticate the sender.

Home and Pi generate separate private keys automatically (mode 0600). Existing
authenticated HTTPS or localhost SSH-tunnel enrollment distributes and pins public
keys. The Pi reports its public key through that same authenticated device link.
No secret/private key is sent over LoRaWAN. Missing enrollment or a changed pin
blocks application transmission: **no plaintext compatibility fallback**. Upgrade
home and car together and establish the trusted enrollment connection before radio
use. Keys are persistent; rotation requires an explicit trusted procedure.

Ciphertext is durably fragmented/reassembled outside the existing transfer frames.
Interruption, reordering and duplicate fragments are supported across restart.
The configured regional application-payload ceiling applies to encrypted fragments,
including an 11-byte ceiling. Encryption adds substantial airtime overhead,
especially at US915 DR0. The modem/network server must still enforce actual
regional/data-rate/channel limits. Normal LoRaWAN headers, ciphertext lengths,
fragment identifiers and MAC polling are visible; application content is encrypted.

The hardware-specific modem utility, OTAA/network credentials and matching US915
sub-band must still be provisioned and physically validated. A generic adapter or
successful software test is not a verified radio link.

The Tiny queues at most one unacknowledged sampled GPS position and produces a
new sample no more than once per five-minute bucket. It retains the full local
track. Home retains authenticated received samples, visible through **Refresh
relayed GPS samples**. The full GPS backlog is not sent automatically by radio.
Gateway mode is currently explicitly **unknown** in this telemetry until a verified
mobile-concentrator state source exists, so these samples cannot falsely train
fixed-gateway coverage. Encrypted telemetry is decoded only at home before the
coverage evidence gate; a plaintext ChirpStack decoder object is no longer trusted.

## Botslab G980H four-channel camera

The G980H is temporary. A separate future USB-camera/Pi 5/AI HAT secondary
dashcam is recorded in [the expansion notes](FUTURE_USB_CAMERA_AI.md); no AI or
USB-camera capture is enabled by that plan.

The user identified the model as the **G980H 4 Channel Dash Cam Set**.
Wi-Fi/Bluetooth alone does not identify its recording-download API.
Direct connection and automatic Wi-Fi downloading remain **pending a verified G980H
download protocol**. The installer does not guess its password, change saved Wi-Fi,
interrupt the hotspot or issue undocumented camera commands.

Files obtained from the camera/app/card can be placed in the inbox, preserving
the camera's four channel directories (for example `front/`, `rear/`, `left/`,
`right/` if those labels match your installation):

```
~/car-console/dashcam-inbox/
```

The normal `car-data` service automatically imports supported MP4/MOV/MKV/TS files
once size/mtime are stable across at least 30 seconds and ffprobe confirms video.
Original channel subdirectories and filenames are kept in metadata. Folder names
are treated as supplied labels, not inferred from undocumented camera filename
codes. Camera-provided creation time, if present, is retained separately from
import time and is not assumed to be an accurate UTC timestamp.
It stores hash-addressed verified local copies, archives them to MooseFS when
available and preserves originals. The reports page lists available recordings
and supports local playback/download with HTTP byte ranges, including through
the Duet proxy without buffering an entire clip. Browser codec support varies;
unsupported media can still be downloaded.

A durable, low-priority queue reports clip name, source, import time, channel and size to
home over the encrypted radio link. Acknowledgments are application receipts,
not just modem TX receipts. They survive interruption; retransmitted catalog
messages are idempotent. The home panel shows received clip details.

**Full video, live streaming and thumbnails are not sent over LoRaWAN.** A 50 MiB
clip alone contains over 419 million bits; at an illustrative uninterrupted
1 kbit/s it would take over 116 hours before framing, retries or airtime limits.
Practical LoRaWAN transfer would be considerably slower. Full recordings use
Ethernet/Wi-Fi; radio provides small encrypted catalog messages.

## Validation limits

Automated tests cover sealed-box round trips, plaintext/wrong-key rejection,
pinned-key changes, restart/reordering/duplicates, small payload ceilings, durable
catalog acknowledgments, verified local archive copies, missing mounts, stable
camera imports and HTTP range streaming through the Duet. Native Ubuntu 24.04
packages have been exercised with generated master/chunkserver configuration.
The cloud lacks `/dev/fuse`, so a real shared mount, physical disconnect/power-loss
recovery, Botslab access and over-air radio delivery still require device testing.
