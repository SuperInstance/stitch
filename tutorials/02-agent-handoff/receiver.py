#!/usr/bin/env python3
"""
Tutorial 02 — the receiver: get a file, check it YOURSELF, book the receive.

The receiver does not trust the producer's hash — it computes the
SHA-256 of the file that actually arrived and books THAT. If the file
was altered in transit, the two bookings disagree and nothing mints
(see derive_receipt.py). The receiver's ledger is its own, on its own
"machine" (here: its own directory; in your deployment, really its own).

Run:  python3 receiver.py --inbox <dir containing report.json> --out <dir>
"""
import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stitch-python"))

import stitch


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    inbox, out = Path(args.inbox), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    delivered = inbox / "report.json"
    digest = hashlib.sha256(delivered.read_bytes()).hexdigest()

    me = stitch.Tenant(identity="receiver")
    ledger = stitch.Ledger(path=str(out / "receiver.jsonl"))
    stitch.declare_budget(me, ledger)
    stitch.book_receive(me, ledger, xchg="delivery-1", peer="producer",
                        payload={"file": "report.json", "sha256": digest})
    ledger.close()
    print(f"receiver: hashed delivered file sha256={digest[:16]}… and booked RECEIVE delivery-1")


if __name__ == "__main__":
    main()
