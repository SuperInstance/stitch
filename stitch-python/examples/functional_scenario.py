#!/usr/bin/env python3
"""
stitch v0.3 — FUNCTIONAL test (not the unit suite).

Story under test (Casey's framing): physical instances act as their own
sovereign selves. Each keeps its own record on its own machine, writes
without a coordinator or a shared clock, exchanges records later by plain
file copy, and independently verifies / agrees. Shared state is minted
only where sovereign ledgers agree.

Subcommands:
  make <identity> <dir>   Write ONLY that identity's ledger, in <dir>.
                          Identities: proart, thor, vm (a 3-self world).
  derive <dir>            Read every ledger in <dir> plus local probes,
                          print a canonical verdict report. The report
                          contains no timestamps, no absolute paths and
                          no head hashes, so the same bundle derived on
                          different machines must be BYTE-IDENTICAL.

Test keys: the proart self signs its ATTEST with a FIXED, PUBLISHED test
secret (32 x 0x07) so every auditor can verify it. This is a test key,
printed here on purpose — it proves the signature mechanism, not a real
identity.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

for _cand in (Path(__file__).parent.parent, Path(__file__).parent / "stitch-python"):
    if (_cand / "stitch").is_dir():
        sys.path.insert(0, str(_cand))
        break

import stitch
from stitch import (
    Tenant, Ledger, KLSurprise, bind, effect, attest, tick,
    book_send, book_receive, declare_budget,
    audit_ledger, agree, agreed_state, agree_n, agreed_state_n,
    verify_anchor, payload_hash,
)
from stitch.crypto import public_key as ed_public_key

TEST_SK = bytes([7] * 32)
TEST_PK = ed_public_key(TEST_SK)
WRONG_PK = ed_public_key(bytes([9] * 32))

P_AGREE = {"doc": "harbor-chart", "rev": 3, "place": "Sitka — 世界"}
P_DISPUTE_SEND = {"doc": "harbor-chart", "rev": 3, "place": "Sitka — 世界"}
P_DISPUTE_RECV = {"doc": "harbor-chart", "rev": 4, "place": "Sitka — 世界"}
P_RING = {"doc": "ring-chart", "rev": 1}

IDS = ["proart", "thor", "vm"]
PEER = {"proart": "thor", "thor": "vm", "vm": "proart"}       # ring successor
PRED = {"proart": "vm", "thor": "proart", "vm": "thor"}       # ring predecessor


def cmd_make(identity: str, outdir: str) -> None:
    if identity not in IDS:
        raise SystemExit(f"unknown identity {identity!r}, want one of {IDS}")
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{identity}.jsonl"
    if path.exists():
        path.unlink()
    me = Tenant(identity=identity)
    ledger = Ledger(path=str(path))
    cell = bind(me, ledger, kind="task",
                payload={"goal": f"chart the harbour, from {identity} — 世界"})
    ok = effect(me, ledger, KLSurprise(), cell, "status",
                predicted={"status": "unknown"}, observed={"status": "alive"})
    assert ok, "first 1-bit effect must fit a fresh tick"
    if identity == "proart":
        attest(me, ledger, cell, claim="present", secret_key=TEST_SK)
    else:
        attest(me, ledger, cell, claim="present")
    tick(me, ledger)

    # Bilateral pair is proart <-> thor.
    if identity == "proart":
        book_send(me, ledger, xchg="x-agree", peer="thor", payload=P_AGREE)
        book_send(me, ledger, xchg="x-dispute", peer="thor", payload=P_DISPUTE_SEND)
    elif identity == "thor":
        book_receive(me, ledger, xchg="x-agree", peer="proart", payload=P_AGREE)
        book_receive(me, ledger, xchg="x-dispute", peer="proart", payload=P_DISPUTE_RECV)

    # N-party ring: each self sends to its successor, receives from predecessor.
    book_send(me, ledger, xchg="ring-1", peer=PEER[identity], payload=P_RING)
    book_receive(me, ledger, xchg="ring-1", peer=PRED[identity], payload=P_RING)

    ledger.close()
    n = sum(1 for _ in open(path, encoding="utf-8"))
    print(f"MAKE {identity}: wrote {path.name} ({n} entries), no other machine contacted")


def _fresh(path: Path):
    if path.exists():
        path.unlink()
    return Ledger(path=str(path))


def derive_report(bundle: Path) -> list[str]:
    L = []
    pa, pt, pv = (bundle / f"{i}.jsonl" for i in IDS)
    tmp = Path(tempfile.mkdtemp(prefix="stitch-probe-"))
    try:
        # A. Each genuine ledger audits clean on its own.
        for name, p in [("proart", pa), ("thor", pt), ("vm", pv)]:
            v = audit_ledger(str(p))
            L.append(f"A audit {name}: chain_ok={v.chain_ok} law_ok={v.law_ok} entries={v.entries}")

        # B. Bilateral mint / refuse.
        v = agree(str(pa), str(pt), "x-agree")
        L.append(f"B agree x-agree: agreed={v.agreed} reason={v.reason} ph={v.payload_hash}")
        L.append(f"B x-agree ph matches payload_hash(P_AGREE): {v.payload_hash == payload_hash(P_AGREE)}")
        v = agree(str(pa), str(pt), "x-dispute")
        L.append(f"B agree x-dispute: agreed={v.agreed} reason={v.reason}")
        minted, refused = agreed_state(str(pa), str(pt))
        L.append(f"B agreed_state: minted={[a.xchg for a in minted]} refused={[(a.xchg, a.reason) for a in refused]}")

        # C. N-party ring mints; the whole fold agrees.
        vn = agree_n([str(pa), str(pt), str(pv)], "ring-1")
        L.append(f"C agree_n ring-1: agreed={vn.agreed} reason={vn.reason} parties={vn.parties} ph={vn.payload_hash}")
        mn, rn = agreed_state_n([str(pa), str(pt), str(pv)])
        L.append(f"C agreed_state_n: minted={[a.xchg for a in mn]} refused={[(a.xchg, a.reason) for a in rn]}")

        # D. Anchor: genuine ledger is anchored; a truncated copy is caught.
        va = agree(str(pa), str(pt), "x-agree")
        anc = verify_anchor(str(pa), va.head_a, va.entries_a)
        trunc = tmp / "proart-truncated.jsonl"
        lines = open(pa, encoding="utf-8").read().splitlines()
        trunc.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        anc_t = verify_anchor(str(trunc), va.head_a, va.entries_a)
        L.append(f"D anchor genuine: status={anc.status}; truncated copy: status={anc_t.status}")

        # E. Forged (law) vs tampered (chain) are caught by different checks.
        forged = tmp / "forged.jsonl"
        led = _fresh(forged)
        led.append({"t": 1000, "ns": "mallory", "op": "EFFECT", "cell": "x", "g": 2.5, "s": 2.5})
        led.append({"t": 2000, "ns": "mallory", "op": "TICK", "g": 0.0})
        led.close()
        vf = audit_ledger(str(forged))
        L.append(f"E forged ledger: chain_ok={vf.chain_ok} law_ok={vf.law_ok}")
        tampered = tmp / "tampered.jsonl"
        raw = open(pa, encoding="utf-8").read()
        assert "alive" in raw
        tampered.write_text(raw.replace("alive", "a1ive", 1), encoding="utf-8")
        vt = audit_ledger(str(tampered))
        L.append(f"E tampered copy of proart: chain_ok={vt.chain_ok} law_ok={vt.law_ok} broken_at={vt.chain_broken_at}")

        # F. Conservation at WRITE time: second 1-bit write in one tick is
        #    rejected, appends nothing, and does not mutate the cell.
        p1 = tmp / "conserve.jsonl"
        led = _fresh(p1)
        t1 = Tenant(identity="probe")
        c1 = bind(t1, led, kind="task", payload={})
        s = KLSurprise()
        ok1 = effect(t1, led, s, c1, "k1", {"k1": "a"}, {"k1": "b"})
        n_before = sum(1 for _ in open(p1, encoding="utf-8"))
        ok2 = effect(t1, led, s, c1, "k2", {"k2": "a"}, {"k2": "b"})
        n_after = sum(1 for _ in open(p1, encoding="utf-8"))
        val = c1.view("k2") if hasattr(c1, "view") else None
        led.close()
        L.append(f"F conservation: first_write={ok1} second_write_same_tick={ok2} "
                 f"entries_before={n_before} entries_after={n_after} cell_k2={val!r}")

        # G. Budgets are position-aware: declaring 3.0 AFTER a 2.0 overspend
        #    does not excuse it; declaring BEFORE a legit spend covers it.
        p2 = tmp / "budget-attack.jsonl"
        led = _fresh(p2)
        big = Tenant(identity="alice", budget=10.0)
        c2 = bind(big, led, kind="task", payload={})
        effect(big, led, s, c2, "k1", {"k1": "a"}, {"k1": "b"})
        effect(big, led, s, c2, "k2", {"k2": "a"}, {"k2": "b"})
        declare_budget(big, led, budget=3.0)
        led.close()
        vb = audit_ledger(str(p2))
        L.append(f"G budget retroactive attack: chain_ok={vb.chain_ok} law_ok={vb.law_ok} budgets={vb.budgets}")
        p3 = tmp / "budget-legit.jsonl"
        led = _fresh(p3)
        big2 = Tenant(identity="alice", budget=10.0)
        declare_budget(big2, led, budget=3.0)
        c3 = bind(big2, led, kind="task", payload={})
        effect(big2, led, s, c3, "k1", {"k1": "a"}, {"k1": "b"})
        effect(big2, led, s, c3, "k2", {"k2": "a"}, {"k2": "b"})
        led.close()
        vl = audit_ledger(str(p3))
        L.append(f"G budget declared-first legit spend: chain_ok={vl.chain_ok} law_ok={vl.law_ok}")

        # H. Signed attest (opt-in): right key verifies, wrong key fails.
        vg = audit_ledger(str(pa), attest_keys={"proart": TEST_PK})
        L.append(f"H signed attest, right key: verified={vg.attest_verified} failures={len(vg.attest_failures)} signatures_ok={vg.signatures_ok}")
        vw = audit_ledger(str(pa), attest_keys={"proart": WRONG_PK})
        L.append(f"H signed attest, wrong key: verified={vw.attest_verified} failures={len(vw.attest_failures)} signatures_ok={vw.signatures_ok}")
        vu = audit_ledger(str(pa))
        L.append(f"H unsigned audit (no keys supplied): verified={vu.attest_verified} failures={len(vu.attest_failures)}")

        # J. KNOWN LIMITATION, probed on purpose: bookings are unsigned and
        #    identity is a string claim, so a fresh impostor ledger that
        #    merely CLAIMS to be 'thor' and mirrors the booking will mint
        #    against proart's genuine ledger. Agreement proves two RECORDS
        #    agree; it does not prove a copy is authentic.
        imp = tmp / "impostor.jsonl"
        led = _fresh(imp)
        imp_t = Tenant(identity="thor")
        book_receive(imp_t, led, xchg="x-agree", peer="proart", payload=P_AGREE)
        led.close()
        vi = agree(str(pa), str(imp), "x-agree")
        L.append(f"J LIMITATION impostor 'thor' ledger mints x-agree: agreed={vi.agreed} reason={vi.reason}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return L


def cmd_derive(outdir: str) -> None:
    for line in derive_report(Path(outdir)):
        print(line)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "make":
        cmd_make(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "derive":
        cmd_derive(sys.argv[2])
    else:
        raise SystemExit(__doc__)
