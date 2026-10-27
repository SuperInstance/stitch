#!/usr/bin/env python3
"""
Tutorial 01 — the supervisor: audit a journal you did not watch happen.

This script never ran the job. Given only journal.jsonl, it answers the
questions a supervisor actually asks:
  - Is this the whole, untampered record?      -> the hash chain
  - Did the agent stay inside its budget?      -> the law replay
  - What budget did it claim, in the record?   -> declared budgets
  - What work does the record show?            -> the entries themselves

Run after work.py:   python3 audit.py
Exit code 0 = audit passed; 1 = failed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stitch-python"))

import stitch

JOURNAL = Path(__file__).parent / "journal.jsonl"


def main() -> int:
    verdict = stitch.audit_ledger(str(JOURNAL))
    print(f"chain_ok : {verdict.chain_ok} (broken at: {verdict.chain_broken_at})")
    print(f"law_ok   : {verdict.law_ok} (max excess: {verdict.law_excess})")
    print(f"entries  : {verdict.entries}")
    print(f"budgets  : {verdict.budgets}  (declared in the ledger itself)")
    print(f"head     : {verdict.head_hash}")

    notes = attest = 0
    for line in open(JOURNAL, encoding="utf-8"):
        e = json.loads(line)
        if e.get("op") == "BIND" and e.get("kind") == "note":
            notes += 1
        if e.get("op") == "ATTEST":
            attest += 1
            print(f"attested : {e.get('claim')!r} by {e.get('ns')}")
    print(f"record shows: {notes} note(s) processed, {attest} attestation(s)")

    print("AUDIT:", "PASS" if verdict.ok else "FAIL")
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
