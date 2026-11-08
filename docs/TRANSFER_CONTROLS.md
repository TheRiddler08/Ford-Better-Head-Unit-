# Signed update delivery controls

Open **Signed update transfers** in the authenticated home control panel. It
lists active Tiny/Duet source changes, with role and finished/history filters.
Use **Load older transfers** to inspect beyond the first 25 rows. Opening a
detail panel stops automatic refresh from replacing what you are reading;
older pages remain in view until you refresh or change filters.

These controls apply to the small signed source-update channel. Full Git,
OS and container updates retain the established Duet courier/IP path. Selected
data files have their own editor and conflict workflow.

## Sender controls

- **Pause sending / Resume sending** preserves the signed descriptor, transfer
  ID, saved data and all device receipts. Pause stops future home-side scheduling
  for this transfer over both IP and radio. It cannot recall transmitted frames,
  remove an update already received by the car, or prevent activation on a
  receiver that already has the complete verified update.
- **Use IP only** excludes this transfer from new LoRaWAN scheduling. IP can
  continue while the ordinary radio path carries other work. **Use automatic
  fallback** allows radio again when recent authenticated IP contact is absent.
- **High priority / Normal priority** changes local ordering among signed
  source transfers. It does not bypass signatures, budgets or regional payload
  limits, and does not grant a source update priority over transport ACKs.
- **Retry missing fragments** makes unconfirmed chunks eligible for another
  attempt. It never deletes acknowledgments or changes the signed identity.
  If every chunk is acknowledged, it re-solicits the final applied receipt.
  Pause settings, IP-only policy, Class A receive windows, spacing and daily
  quotas still apply. A click is not an immediate over-air transmission.

Applied and failed transfers cannot be reopened using these controls. For a
receiver rejection, inspect the reported error, load its latest source copy and
publish a corrected signed change. A late applied receipt still completes a
paused transfer; the panel reports the actual receiver result.

Control revisions reject outdated browser actions. The device token cannot
change sender controls; they require the separate owner panel token. Scheduler
metadata is stored separately from signed manifests, so a preference change
cannot authorize different executable bytes.

## Prefer an existing IP connection

A successfully processed authenticated car IP exchange records contact for its
role. The home scheduler holds source-update and selected-file radio downlinks
for that role for 45 seconds. Normal car exchanges run every 10 seconds. When
they stop, automatic radio eligibility resumes after the bounded hold expires;
the same saved chunks, receipts and encrypted framing continue.

This hold is per car role. A Tiny with IP contact does not disable a Duet's
radio transfers. It also does not suppress radio transport ACKs, health messages
or diagnostics. Legacy settings keep their existing scheduler. Contact is an
observed authenticated request, not proof of permanent reachability.

## Interpret progress and airtime

Manifest and data receipts are shown separately. **100% fragments received** is
distinct from **applied**: the final activation receipt is still required. Last
send queueing, last device receipt, last transport and last observed IP contact
have separate timestamps. No source contents, private keys or tokens are exposed
by the inspector's status API.

The shared home downlink budget shows persisted reservations, spacing, quota
reset time and next eligible reservation. A reservation or broker publication
is not proof that a radio frame reached the car. Failed/uncertain attempts can
consume a reservation conservatively. Counters persist across service restarts;
retry and priority controls cannot reset them. The daily quota uses UTC periods;
the browser displays the reset timestamp in its local timezone.

The encrypted frame estimate counts one full pass of unconfirmed application
chunks using the configured encrypted payload budget. It includes the existing
sealed-box and framing overhead without generating/transmitting packets. It
does not know partial encrypted packets at the receiver, lost receipts, retries,
gateway scheduling or how much quota other work will use. When only an applied
receipt is missing, it estimates one final data-chunk re-solicitation. It is not
a delivery-time estimate or a promise that the link is provisioned.

Home Home Assistant also exposes paused-update counts and distinguishes a
paused sender from an active receiving transfer. Device values remain governed
by their own observation freshness, independently of software scheduling.
