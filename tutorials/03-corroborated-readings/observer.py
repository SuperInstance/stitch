#!/usr/bin/env python3
"""
Tutorial 03 — an observer: record one reading, sovereign and offline.

Each observer is independent: its own identity, its own ledger, no
connection to the others at record time. It writes two things into
--out: its ledger (<name>.jsonl) and its delivered reading
(<name>.reading.json) — the pair a collector later picks up by plain
file copy, whenever transport happens.

The observer books a SEND of its reading to the collector in its own
ledger. Minting still needs the collector's mirror (collector.py).

Run:  python3 observer.py --name obs-a --reading 21.5 --out out/obs-a
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stitch-python"))

import stitch

EVENT = "harbour-temp-0900"   # ADAPT: your event/exchange namespace
UNIT = "degC"                 # ADAPT: your unit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--reading", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    payload = {"event": EVENT, "reading": args.reading, "unit": UNIT}
    me = stitch.Tenant(identity=args.name)
    ledger = stitch.Ledger(path=str(out / f"{args.name}.jsonl"))
    stitch.declare_budget(me, ledger)
    cell = stitch.bind(me, ledger, kind="observation", payload=payload)
    stitch.attest(me, ledger, cell, claim=f"observed {args.reading} {UNIT}")
    stitch.book_send(me, ledger, xchg=args.name, peer="collector",
                     payload=payload)
    ledger.close()

    (out / f"{args.name}.reading.json").write_text(
        json.dumps({"observer": args.name, **payload}, indent=2) + "\n",
        encoding="utf-8")
    print(f"{args.name}: recorded reading {args.reading} {UNIT}, booked SEND {args.name}")


if __name__ == "__main__":
    main()
