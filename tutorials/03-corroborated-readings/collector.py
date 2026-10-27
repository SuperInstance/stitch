#!/usr/bin/env python3
"""
Tutorial 03 — the collector: pick up delivered readings, book the receives.

The collector reads each *.reading.json that transport dropped into
--inbox and books a RECEIVE for it in its own ledger — recording the
reading exactly as delivered to it, from its own sovereign record.
It does not edit, average, or judge the readings; that is
corroborate.py's job, and it works from the ledgers alone.

Run:  python3 collector.py --inbox out/inbox --out out/collector
"""
import argparse
import json
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

    me = stitch.Tenant(identity="collector")
    ledger = stitch.Ledger(path=str(out / "collector.jsonl"))
    stitch.declare_budget(me, ledger)
    for f in sorted(inbox.glob("*.reading.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        payload = {"event": d["event"], "reading": d["reading"], "unit": d["unit"]}
        stitch.book_receive(me, ledger, xchg=d["observer"],
                            peer=d["observer"], payload=payload)
        print(f"collector: booked RECEIVE {d["observer"]} "
              f"(reading {d['reading']} {d['unit']})")
    ledger.close()


if __name__ == "__main__":
    main()
