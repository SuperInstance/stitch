#!/usr/bin/env python3
"""
Tutorial 02 — the receipt: derive shared state from the two ledgers.

Anyone holding producer.jsonl and receiver.jsonl — either agent, or a
third-party witness who was never online at delivery time — runs this
and gets the same verdict. Minted = the receipt. Refused = no receipt,
with a distinct reason. Nothing here writes to either ledger.

Run:  python3 derive_receipt.py --producer <producer.jsonl> --receiver <receiver.jsonl>
Exit code 0 = minted; 1 = refused.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stitch-python"))

import stitch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--producer", required=True)
    ap.add_argument("--receiver", required=True)
    args = ap.parse_args()

    v = stitch.agree(args.producer, args.receiver, "delivery-1")
    print(f"agreed={v.agreed} reason={v.reason}")
    if v.agreed:
        print(f"RECEIPT: {v.sender} -> {v.receiver}, payload_hash={v.payload_hash}")
        print(f"pinned heads: producer={v.head_a} receiver={v.head_b}")
        anchor = stitch.verify_anchor(args.producer, v.head_a, v.entries_a)
        print(f"producer ledger vs its pin: {anchor.status}")
        return 0
    print(f"NO RECEIPT: {v.detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
