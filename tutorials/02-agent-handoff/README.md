# 02 — The agent-to-agent handoff

**The job:** a producer agent finishes work — here, a summary report
from sensor readings — and delivers it to a receiver agent on another
machine. Both need a **receipt**: proof, derivable later by anyone,
that the thing the receiver got is bit-for-bit the thing the producer
sent. No shared server, no shared clock, no trust in the transport.

This is the template for agent subcontracting, deliverable-for-payment
flows, and any "you said / I said" boundary between systems.

## Run it

```bash
cd tutorials/02-agent-handoff
bash run_demo.sh
```

The demo runs the same delivery twice — once clean, once with a single
byte altered in transit. Real output (hashes abbreviated):

```
producer: wrote report.json sha256=59511105be0a23bb… and booked SEND delivery-1
receiver: hashed delivered file sha256=59511105be0a23bb… and booked RECEIVE delivery-1
agreed=True reason=agreed
RECEIPT: producer -> receiver, payload_hash=875245192b…
producer ledger vs its pin: anchored
```
```
receiver: hashed delivered file sha256=0dfbcb18eeeba1b0…   <-- its own hash, of what ARRIVED
agreed=False reason=payload_mismatch
NO RECEIPT: payload hash mismatch for 'delivery-1' …
```

Or drive the three scripts yourself — each is one side of the story,
and each runs alone:

```bash
python3 producer.py --out out/producer
cp out/producer/report.json <wherever the receiver lives>   # any transport
python3 receiver.py --inbox <that dir> --out out/receiver
python3 derive_receipt.py --producer out/producer/producer.jsonl \
                          --receiver out/receiver/receiver.jsonl
```

## What just happened

- **The payload is a contract, not the cargo.** The booking payload is
  `{"file": "report.json", "sha256": <hash of the file bytes>}`. The
  file itself travels by any transport — copy, email, object storage.
  stitch only ever carries the *commitment*.
- **Each side hashes independently.** The receiver never sees the
  producer's hash; it hashes what arrived and books that. Agreement
  compares the two sovereign records, not the two agents' claims in a
  chat.
- **Minting writes nothing.** `derive_receipt.py` derives the receipt
  from the two files; either agent — or a third-party witness holding
  copies — gets the identical verdict at any later time.
- **Every refusal is distinct.** `payload_mismatch` (above) is a
  different fact from `missing_mirror` (receiver never booked),
  `peer_mismatch` (booked against the wrong counterpart), or
  `chain_broken` / `law_violated` (a ledger itself fails audit). Your
  code should branch on `verdict.reason` — it's a value, not an
  exception.
- **The anchor pins the past.** The minted verdict pins both ledgers'
  head hashes. If the producer's ledger is later truncated,
  `verify_anchor` against the pin reports `truncated` even though the
  shortened ledger still verifies on its own.

## Adapt it

The payload dict in `producer.py` (marked ADAPT) is your contract:
file hash for artifacts, `{amount, currency, invoice}` for payments,
`{commit, repo}` for code delivery — anything both sides can compute
*independently* from what they hold. Exchange ids are your namespace:
one per delivery, or per delivery attempt (`delivery-1`, `delivery-2`…)
so retries never collide with history. Run each agent's script on its
own machine and move only the ledger files (or copies) to whoever
derives receipts.

## Limits, honestly

The receipt proves the two **records** agree about the delivered
bytes. It does not prove the report is *correct*, that the receiver
read it, or that a ledger copy shown to a witness is authentic — a
fresh ledger merely claiming the identity `producer` and mirroring
the booking would also mint (identity is a string claim). Signed
attestations narrow the authenticity gap for ATTEST entries; bookings
themselves are unsigned in v0.3.

## Exercises

1. Sign the producer's attestation: generate a key with
   `stitch.crypto.Key.generate()`, pass `secret_key=` to `attest`, and
   audit the producer's ledger with
   `audit_ledger(..., attest_keys={"producer": <public key hex>})`.
   Check `verdict.signatures_ok`, and how it differs from `verdict.ok`.
2. Truncate the producer's ledger by two lines after a mint and run
   `derive_receipt.py` again — then `verify_anchor` with the original
   pin. Which check sees it, and why does the other one not?
