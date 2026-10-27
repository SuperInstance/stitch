# 03 — Corroborated observers

**The job:** several independent observers — sensors, devices, field
agents — record the same event while offline. Later their records are
gathered, and the question is: *which version of the event do
independent records actually support?* One observer may be wrong; one
may be tampered with after the fact. Neither should decide the answer
alone.

This is the template for sensor corroboration, distributed logging,
multi-witness reporting, and fleet telemetry that syncs by file copy.

## Run it

```bash
cd tutorials/03-corroborated-readings
bash run_demo.sh
```

The demo fields four observers of one harbour temperature: two read
21.5 °C, a third also read 21.5 °C but its ledger file is altered
afterwards, and a fourth honestly reports 99.9 °C (a broken sensor).
Real output (hashes abbreviated):

```
obs-a: minted with collector, payload_hash=8b9980a06828c42d…
obs-b: minted with collector, payload_hash=8b9980a06828c42d…
obs-c: EXCLUDED — ledger fails audit (chain_ok=False, law_ok=True)
obs-d: minted with collector, payload_hash=02a474259f9360c1…

CORROBORATED: 2 observer(s) ['obs-a', 'obs-b'] payload_hash=8b9980a06828c42d…
below quorum: 1 observer(s) ['obs-d'] payload_hash=02a474259f9360c1…
```

Or run the pieces yourself: `observer.py --name X --reading V --out DIR`
per observer (each on its own machine, if you like), copy each
`*.reading.json` to an inbox, `collector.py --inbox … --out …`, then
`corroborate.py --dir … --quorum 2`.

## What just happened

- **Recording needs nobody.** Each observer wrote its own ledger and
  booked a SEND of its reading to the collector, entirely offline.
  There is no moment where observers coordinate; coordination is
  deferred to derive time — that's what makes this shape scale to
  devices that meet rarely, or never.
- **Minting is per-observer, and it means one narrow thing:** *this
  observer's own record and the collector's record of what it
  received are the same reading.* obs-d's broken 99.9 mints — the
  collector honestly received it. Minting attests the record, never
  the reading's truth.
- **Corroboration is your fold, not a stitch.** `corroborate.py`
  groups mints by payload hash and applies a quorum (2 here). The
  21.5 reading is corroborated because two *independent* ledgers the
  collector never wrote agree on it.
- **Tampering excludes itself.** obs-c's altered ledger fails audit
  before agreement is even attempted, so its (delivered, intact)
  reading file cannot rescue it — the sovereign record is what
  counts, and it no longer verifies.

## Adapt it

`EVENT`/`UNIT` and the reading payload in `observer.py` are yours:
votes, measurements, sightings, log digests — any value several
parties can record independently. The collector pattern (one ledger,
one RECEIVE per reporter) fits a hub; for a ring of peers with no hub,
use `agree_n` over all ledgers instead (SPEC, "N-party agreement") —
the demo in `stitch-python/examples/functional_scenario.py` shows the
ring booking pattern running.

## Limits, honestly

Quorum counts *records*, and records are only as independent as the
observers really are: two ledgers written by one process are one
witness wearing two hats. A fresh impostor ledger claiming an
observer's identity and mirroring its booking would mint — identity
is a string claim, bookings are unsigned. Where witnesses matter,
pair this pattern with signed attestations and out-of-band keys, and
treat corroboration as evidence, not proof.

## Exercises

1. Raise the quorum to 3 and watch the demo fail to corroborate —
   then repair obs-c (re-run its observer step) and see what changes.
2. Add a fifth observer whose reading file is altered *in transit*
   (before the collector books it). Which verdict appears, and how
   does it differ from obs-c's?
