#!/usr/bin/env python3
"""
Tutorial 03 — corroboration: which readings do independent records support?

For each observer, agree() asks: did this observer's own ledger and the
collector's ledger record the SAME reading? A mint means exactly that —
no more, no less. Corroboration is the application-level fold on top:
a reading counts as corroborated when at least --quorum INDEPENDENT
observers mint the identical payload.

An observer whose ledger fails audit (tampered, law-violating) mints
nothing. An observer who honestly recorded a different reading mints
their reading — it just stands alone below quorum. stitch never claims
the corroborated reading is TRUE; it proves independent records agree.

Run:  python3 corroborate.py --dir out --quorum 2
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stitch-python"))

import stitch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--quorum", type=int, default=2)
    args = ap.parse_args()
    root = Path(args.dir)
    collector = root / "collector" / "collector.jsonl"

    groups = defaultdict(list)   # payload_hash -> [observer names]
    values = {}                  # payload_hash -> reading payload
    for obs_dir in sorted((root / "observers").iterdir()):
        name = obs_dir.name
        ledger = obs_dir / f"{name}.jsonl"
        audit = stitch.audit_ledger(str(ledger))
        if not audit.ok:
            print(f"{name}: EXCLUDED — ledger fails audit "
                  f"(chain_ok={audit.chain_ok}, law_ok={audit.law_ok})")
            continue
        v = stitch.agree(str(ledger), str(collector), name)
        if v.agreed:
            groups[v.payload_hash].append(name)
            print(f"{name}: minted with collector, payload_hash={v.payload_hash[:16]}…")
        else:
            print(f"{name}: no mint ({v.reason})")

    print()
    corroborated = False
    for ph, names in groups.items():
        mark = "CORROBORATED" if len(names) >= args.quorum else "below quorum"
        if len(names) >= args.quorum:
            corroborated = True
        print(f"{mark}: {len(names)} observer(s) {names} payload_hash={ph[:16]}…")
    return 0 if corroborated else 1


if __name__ == "__main__":
    raise SystemExit(main())
