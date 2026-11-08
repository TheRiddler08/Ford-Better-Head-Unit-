# Selected data files across radio and IP

`car-file-sync` and `car-home-sync` are installed through the normal role/home
installers. Select the same basenames at both ends. Nothing is selected by
default: existing navigation routes, GPS history, camera files, databases and
system configuration are not automatically uploaded.

On a car device, merge into `/etc/car-dashboard/update-link.json` (retain its
existing pairing fields):

```json
"sync_files": ["notes.txt", "preferences.json"]
```

The selected originals live in `~/car-console/sync/` on that car role. On the
home server, create `~/.local/share/car-home-control/sync-settings.json`:

```json
{"files":["notes.txt","preferences.json"]}
```

Home copies live under `~/.local/share/car-home-control/sync/tiny/`, `/duet/`,
`/pi/` or `/laptop/`. You can edit either endpoint's copy. Only selected safe
`.txt`/`.json` basenames are accepted: no paths, scripts, symlinks or executable
installation. At most four files per role, each up to 16 KiB, are supported.
Removing a file locally does not silently delete the other endpoint's copy.

The sender computes a delta against its last confirmed baseline. Every transfer
has a persistent ID, base SHA256 and target SHA256. Small delta objects (at most
800 encoded bytes) use the existing authenticated event fragmentation and sealed
radio transport; bigger changes remain `waiting-ip`. The existing authenticated
home tunnel/HTTPS exchange carries those larger objects automatically. No plain
HTTP or secret-bearing redirects are added. Radio/IP share the same pending
transfer identity; the receiver is idempotent across restarts and handoffs.

Receiving a radio fragment or transport ACK does not mean a file was applied.
An application receipt confirms verified target bytes and an atomic publish.
Concurrent edits create a conflict and preserve both originals rather than
silently overwriting one. An already-completed transfer cannot overwrite a later
local edit when it is replayed. Resolve conflicting originals deliberately; the
service will not guess which user's edit should win.

The local `file-sync-status.json` exposes each selected filename, pending ID and
phase. Home HA exposes pending counts, conflicts and sync state per role. A state
of `idle` means no transfer has run yet; `last-transfer-applied` describes the
last confirmed transfer, not a claim that an unplugged device is currently
reachable. `not-selected` means the receiving endpoint has not allowed the file.

A local file that is oversized, unreadable, symlinked or invalid UTF-8 is shown
as `blocked` with a short reason. That file's pending sends pause; its original,
baseline and transfer ID are retained. Other selected files and signed software
updates continue. Repair the file in its existing folder to resume. An unwritable
receiving folder produces no applied receipt, so the sender retains its copy
and retries; repairing the destination allows the same transfer to finish.
Removing a blocked original does not authorize deletion on the other endpoint.

Executable UI/recorder/Duet source updates keep their independent Ed25519 signed
manifest and safe activation path. This data service never relaxes those source
allowlists or executes received text. Small data objects use the existing shared
application authentication and public-key radio encryption; the ordinary car
LAN remains the trust boundary for enrolled role labels.

When IP is healthy, the car sync worker lets that exchange carry pending data;
radio remains a fallback for small deltas. Durable receive state, acknowledgments
and applied receipts survive a transport change. Full video, maps, OS/container
images and files above this small-data limit remain outside this channel.
