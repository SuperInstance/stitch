#!/usr/bin/env python3
"""
Tutorial 02 — the producer: do a job, deliver the product, book the send.

THE JOB (adapt this part): read readings.csv, summarise it into
report.json. The producer writes report.json into --out, keeps its own
ledger there, and books the delivery: the booking payload carries the
SHA-256 of the exact file bytes it produced, so the receiver can check
the delivered file against its own independent hash — stitch never has
to carry the payload itself.

Run (see run_demo.sh for the full happy/tamper walkthrough):
  python3 producer.py --out out/happy/producer
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stitch-python"))

import stitch

HERE = Path(__file__).parent


def build_report() -> dict:
    rows = list(csv.DictReader(open(HERE / "readings.csv", encoding="utf-8")))
    wind = [float(r["wind_knots"]) for r in rows]
    return {
        "source": "readings.csv",
        "readings": len(rows),
        "mean_wind_knots": round(sum(wind) / len(wind), 2),
        "max_wind_knots": max(wind),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="producer's work directory")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    report = build_report()
    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()

    me = stitch.Tenant(identity="producer")
    ledger = stitch.Ledger(path=str(out / "producer.jsonl"))
    stitch.declare_budget(me, ledger)
    # ADAPT: this payload is the contract between the two agents. It
    # names the product and pins its bytes. Keep it small; the product
    # itself travels by whatever transport you already use.
    payload = {"file": "report.json", "sha256": digest}
    stitch.book_send(me, ledger, xchg="delivery-1", peer="receiver",
                     payload=payload)
    cell = stitch.bind(me, ledger, kind="delivery", payload=payload)
    stitch.attest(me, ledger, cell, claim="report delivered")
    ledger.close()
    print(f"producer: wrote {report_path} sha256={digest[:16]}… and booked SEND delivery-1")


if __name__ == "__main__":
    main()
