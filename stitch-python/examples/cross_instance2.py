"""
Cross-instance exercise v0.2 — sovereign selves that agree (or refuse to).

The v0.1 exercise (cross_instance.py) proved independent *verification*:
four machines, each with its own ledger, auditing a bundle of files and
reaching identical verdicts. But the audit was glue in the example, and
nothing in v0.1 could mint shared state. This exercise runs the v0.2
library API instead — `book_send`, `book_receive`, `agreed_state` are
library functions now, not example code:

  make    Each machine runs as its own tenant identity, writing its own
          ledger on its own disk. Paired selves book two exchanges with
          each other:
            x-agree   — both sides book the same payload. Must mint.
            x-dispute — the receiver books a DIFFERENT payload than the
                        sender sent. Must refuse (payload_mismatch).
          An unpaired self (the witness) books nothing; it only derives.
  derive  Given a directory of ledger files (exchanged by any means —
          scp, USB stick, carrier pigeon), fold every pair of ledgers
          through stitch.agreed_state and print the result: which
          exchanges minted, which were refused, and why. Timestamps are
          never consulted; there is no global clock here.

The derive output is fully deterministic (sorted pairs, sorted exchange
ids, reason codes, truncated payload hashes), so the outputs of several
machines can be diffed byte-for-byte: selves that have never met must
derive *identical* shared state from the same files.

Usage:
  python cross_instance2.py make   --identity orin --dir /tmp/interop2 \
      --partner thor --role receiver
  python cross_instance2.py make   --identity vm --dir /tmp/interop2
  python cross_instance2.py derive --dir /tmp/interop2-bundle
"""

import argparse
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import stitch
from stitch import Tenant, Ledger, bind, tick, book_send, book_receive

AGREE_PAYLOAD = {"cargo": "harbor-chart", "rev": 3}
DISPUTE_SENT = {"cargo": "tide-table", "rev": 1}
DISPUTE_RECEIVED = {"cargo": "tide-table", "rev": 2}  # not what was sent


def cmd_make(args) -> None:
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.identity}.jsonl"
    if path.exists():
        path.unlink()
    me = Tenant(identity=args.identity)
    ledger = Ledger(path=str(path))
    bind(me, ledger, kind="self", payload={"from": args.identity})
    if args.partner:
        if args.role == "sender":
            book_send(me, ledger, "x-agree", args.partner, AGREE_PAYLOAD)
            book_send(me, ledger, "x-dispute", args.partner, DISPUTE_SENT)
        else:
            book_receive(me, ledger, "x-agree", args.partner, AGREE_PAYLOAD)
            book_receive(me, ledger, "x-dispute", args.partner,
                         DISPUTE_RECEIVED)
    tick(me, ledger)
    ledger.close()
    role = args.role if args.partner else "witness"
    print(f"{args.identity}: wrote {path} (role={role}"
          + (f", partner={args.partner}" if args.partner else "") + ")")


def cmd_derive(args) -> None:
    files = sorted(Path(args.dir).glob("*.jsonl"))
    if len(files) < 2:
        print(f"derive: need at least 2 ledger files in {args.dir}")
        raise SystemExit(1)
    for fa, fb in itertools.combinations(files, 2):
        agreed, refused = stitch.agreed_state(fa, fb)
        for v in agreed:
            print(f"MINT   {fa.name}+{fb.name} {v.xchg} "
                  f"{v.sender}->{v.receiver} ph={v.payload_hash[:12]}")
        for v in refused:
            print(f"REFUSE {fa.name}+{fb.name} {v.xchg} {v.reason}")


def main() -> None:
    ap = argparse.ArgumentParser(description="stitch cross-instance exercise v0.2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make")
    p.add_argument("--identity", required=True)
    p.add_argument("--dir", required=True)
    p.add_argument("--partner")
    p.add_argument("--role", choices=["sender", "receiver"])
    p = sub.add_parser("derive")
    p.add_argument("--dir", required=True)
    args = ap.parse_args()
    if args.cmd == "make" and bool(args.partner) != bool(args.role):
        ap.error("--partner and --role must be given together")
    {"make": cmd_make, "derive": cmd_derive}[args.cmd](args)


if __name__ == "__main__":
    main()
