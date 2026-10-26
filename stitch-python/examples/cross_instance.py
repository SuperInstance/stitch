"""
Cross-instance exercise — several machines, each a sovereign stitch self.

This is an *exercise*, not a library feature. stitch v0.1 has no transport,
no sync protocol, and no cross-ledger agreement operation. What it does have
is: a self-contained append-only ledger file per tenant, a hash chain anyone
can replay, and a conservation law whose inputs (s, g, TICK boundaries) are
all recorded in the ledger itself. This script tests how far that gets you
when several independent selves meet asynchronously:

  make    Each machine runs this as its own tenant identity, writing its own
          ledger on its own disk. No other machine is contacted.
  forge   Create a "cheating" ledger: entries written with the raw Ledger
          API claiming an EFFECT of s=2.5 bits in one tick (budget is
          log2(3) ~= 1.585). The hash chain is perfectly valid — the law is
          not. Chain verification alone cannot catch this; a law audit can.
  tamper  Copy a good ledger and alter one character of recorded history.
          The hash chain must catch this.
  audit   Given a directory of ledger files (exchanged by any means — scp,
          USB stick, carrier pigeon), independently verify each one:
          (1) hash chain integrity, (2) conservation-law audit — replay the
          recorded s values per tenant per tick and check the recorded g
          never exceeds the budget and always matches the running sum.
          Timestamps are never consulted: there is no global clock here,
          and the audit does not need one.

The auditor assumes the default budget C = log2(3) for every tenant, because
v0.1 ledgers do not record a tenant's configured budget. That assumption is
printed with every verdict — it is a real limitation, not a detail.

Usage:
  python cross_instance.py make   --identity orin --dir /tmp/interop
  python cross_instance.py forge  --dir /tmp/interop
  python cross_instance.py tamper --dir /tmp/interop --source orin.jsonl
  python cross_instance.py audit  --dir /tmp/interop
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import stitch
from stitch import Tenant, Ledger, KLSurprise, bind, effect, attest, tick

BUDGET = stitch.LOG2_3
EPS = 1e-9


def cmd_make(args) -> None:
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.identity}.jsonl"
    if path.exists():
        path.unlink()
    me = Tenant(identity=args.identity)
    ledger = Ledger(path=str(path))
    cell = bind(me, ledger, kind="self", payload={"from": args.identity,
                                                  "msg": f"hello from {args.identity}"})
    ok = effect(me, ledger, KLSurprise(), cell, "status",
                predicted={"status": "unknown"}, observed={"status": "alive"})
    assert ok, "effect should fit in a fresh tick's budget"
    attest(me, ledger, cell, claim="present")
    tick(me, ledger)
    ledger.close()
    print(f"{args.identity}: wrote {path} (4 entries, gamma spent 1.0 of {BUDGET:.4f})")


def cmd_forge(args) -> None:
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "forged.jsonl"
    if path.exists():
        path.unlink()
    # Raw Ledger appends — this bypasses the stitch layer's budget check,
    # exactly what a buggy or dishonest writer implementation would do.
    ledger = Ledger(path=str(path))
    t0 = time.time_ns()
    ledger.append({"t": t0, "ns": "mallory", "op": "BIND", "cell": "c0ffee",
                   "kind": "task", "payload": {}, "parent": None, "g": 0.0})
    ledger.append({"t": t0 + 1, "ns": "mallory", "op": "EFFECT", "cell": "c0ffee",
                   "k": "status", "v": "done", "s": 2.5, "g": 2.5})
    ledger.close()
    print(f"forged: wrote {path} (chain-valid, claims s=2.5 in one tick)")


def cmd_tamper(args) -> None:
    src = Path(args.dir) / args.source
    dst = Path(args.dir) / "tampered.jsonl"
    text = src.read_text(encoding="utf-8")
    # Change one recorded fact (a payload value) without touching hashes.
    tampered = text.replace('"alive"', '"a1ive"', 1)
    assert tampered != text, "tamper target string not found in source ledger"
    dst.write_text(tampered, encoding="utf-8")
    print(f"tampered: wrote {dst} (one recorded value altered, hashes untouched)")


def audit_ledger(path: Path) -> str:
    # (1) Hash chain
    ledger = Ledger(path=str(path))
    chain_ok = ledger.verify()
    entries = list(ledger.read_all())
    ledger.close()
    # (2) Conservation-law audit: replay s per tenant, reset on TICK,
    #     compare every recorded g against the running sum.
    gamma: dict[str, float] = {}
    law_problems: list[str] = []
    for e in entries:
        ns = e.get("ns", "?")
        g = gamma.get(ns, 0.0)
        if e["op"] == "EFFECT":
            g += float(e.get("s", 0.0))
        elif e["op"] == "TICK":
            g = 0.0
        gamma[ns] = g
        recorded = float(e.get("g", 0.0))
        if abs(recorded - g) > 1e-6:
            law_problems.append(f"recorded g={recorded} != replayed gamma={g:.4f} at op {e['op']}")
        if g > BUDGET + EPS:
            law_problems.append(f"gamma={g:.4f} exceeds budget {BUDGET:.4f} (tenant {ns}, op {e['op']})")
    chain = "OK" if chain_ok else "BROKEN"
    law = "OK" if not law_problems else "VIOLATED: " + "; ".join(law_problems)
    return f"{path.name}: entries={len(entries)} chain={chain} law={law}"


def cmd_audit(args) -> None:
    files = sorted(Path(args.dir).glob("*.jsonl"))
    if not files:
        print(f"audit: no .jsonl files in {args.dir}")
        raise SystemExit(1)
    print(f"audit by {args.identity}: assuming budget C=log2(3)={BUDGET:.4f} for all tenants "
          f"(v0.1 ledgers do not record per-tenant budgets); timestamps ignored")
    for f in files:
        print("  " + audit_ledger(f))


def main() -> None:
    ap = argparse.ArgumentParser(description="stitch cross-instance exercise")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make");   p.add_argument("--identity", required=True); p.add_argument("--dir", required=True)
    p = sub.add_parser("forge");  p.add_argument("--dir", required=True)
    p = sub.add_parser("tamper"); p.add_argument("--dir", required=True); p.add_argument("--source", required=True)
    p = sub.add_parser("audit");  p.add_argument("--dir", required=True); p.add_argument("--identity", default="auditor")
    args = ap.parse_args()
    {"make": cmd_make, "forge": cmd_forge, "tamper": cmd_tamper, "audit": cmd_audit}[args.cmd](args)


if __name__ == "__main__":
    main()
