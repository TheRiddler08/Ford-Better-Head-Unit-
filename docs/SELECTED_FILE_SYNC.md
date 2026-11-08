# Selected data files across radio and IP

`car-file-sync` and `car-home-sync` are installed through the normal role/home
installers. Select the same basenames at both ends. Nothing is selected by
default: existing navigation routes, GPS history, camera files, databases and
system configuration are not automatically uploaded.

## Manage from the home panel

The authenticated home panel now includes **Selected data files**. Choose the
Tiny, Duet, Pi or laptop role, enter up to four basenames, and save that role's
home selection. Existing copies are retained when a name is removed. The panel
shows the car's last reported selection and its timestamp after an authenticated
IP exchange; it does not mistake that old report for current connectivity.
Car allowlists remain local and are not silently expanded by home selection.

Open a selected home copy to edit its text, or create a new selected file. JSON
edits must parse as JSON. Saving makes a normal data delta and shows the pending
transfer; it does not claim remote application. Edited bytes stay in the browser
tab's memory, and are not saved to browser storage. Unsaved edits must be saved
or explicitly discarded before opening another copy or changing roles. The
download button saves the current editor text. Windows line endings are
preserved when editing a file that uses CRLF.

Saving uses the loaded content hash. An outdated tab is rejected rather than
overwriting a newer copy. The editor and automatic receivers share a Linux file
lock so concurrent application writers serialize their updates.

### Review a conflict

The editor shows the home original alongside the **reported car copy**. A car
copy can only be reconstructed when the saved common baseline and target hash
verify. Without that baseline the choice buttons stay disabled; compare the
originals manually rather than guessing the incoming bytes.

Choose **Use reported car copy**, **Keep saved home copy**, or **Use edited
text**. The home original is backed up before it is replaced. Keeping or merging
the home copy first durably accepts the verified car baseline, then queues a
return delta with a new identity. The car's applied receipt is still required
to finish that return transfer. Stale receipts or replayed old transfers cannot
overwrite a later local edit.

Resolution choices are journaled before file replacement. If interrupted, the
normal home sync worker resumes them. If a newer external edit appears during
recovery, it keeps that edit and shows the earlier saved choice for review.
Unrelated files and signed software updates continue while a resolution is
blocked. No plaintext radio path or source-update privilege is added.

Private backups are ordinary files next to the file journal: for example,
`~/.local/share/car-home-control/sync-state/tiny-backups/` at home. There are at
most 32 backup files per role. At capacity, replacements pause until you archive
older backups elsewhere; originals are never silently deleted to make room.
Up to 32 unresolved received conflicts are retained at a time. Reviewing one
marks older conflicts for that filename resolved and frees unresolved capacity.

## Local configuration

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

That legacy `files` list remains the fallback for all roles. The panel preserves
it and writes independent overrides under `roles`, for example:

```json
{"files":[],"roles":{"tiny":["notes.txt"],"duet":["preferences.json"]}}
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
