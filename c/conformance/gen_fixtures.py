#!/usr/bin/env python3
"""
gen_fixtures.py — build the stitch-c conformance fixtures with the
Python reference implementation and record the exact verdict
reports the C CLI must reproduce.

Layout (all paths relative to this script's fixtures/ dir):
  scenario2-py/{alice,bob}.jsonl   the scenario2 Python twin
  rich/{alice,bob}.jsonl           = scenario2-py pair (agree/state)
  audit/<name>.jsonl               audit fixtures, each with a
                                   matching <name>.expected report
  pairs/<name>/{a,b}.jsonl         agree fixtures + <name>.expected
                                   (and <name>.rev.expected for the
                                   swapped-argument call)
  anchors/...                      verify_anchor fixtures + reports
  floats.txt / floats.expected     uint64 bit patterns -> json float
  canon.txt / canon.expected       JSON texts -> canonical texts
  phash.txt / phash.expected       JSON texts -> payload hashes

Reports use the same key=value format as the stitchc CLI.
"""
import hashlib
import json
import os
import random
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "stitch-python"))

FX = HERE / "fixtures"
FX.mkdir(exist_ok=True)
os.chdir(FX)

import stitch
import stitch.stitches as stitches_mod
from stitch import (Tenant, Ledger, KLSurprise, bind, link, effect, tick,
                    attest, delegate, contest, merger, revoke, withdraw,
                    declare_budget)
from stitch.agreement import (audit_ledger, agree, agreed_state,
                              payload_hash, verify_anchor, book_send,
                              book_receive)
from stitch.core import LOG2_3, FNV_OFFSET

ORIG_NOW = stitches_mod._now_ns


class Clock:
    """Shared deterministic clock: counter * step, pre-incremented.

    One instance is installed globally, so both tenants in a pair
    share the counter — exactly like the C scenario drivers."""

    def __init__(self, step):
        self.n = 0
        self.step = step

    def __call__(self):
        self.n += 1
        return self.n * self.step


def install(step):
    clock = Clock(step)
    stitches_mod._now_ns = clock
    return clock


def restore():
    stitches_mod._now_ns = ORIG_NOW


def b(x):
    return "true" if x else "false"


def fmt_audit(v):
    budgets = "-"
    if v.budgets:
        budgets = ",".join(f"{k}={v.budgets[k]!r}" for k in sorted(v.budgets))
    lines = [
        f"entries={v.entries}",
        f"chain_ok={b(v.chain_ok)}",
        f"chain_broken_at={v.chain_broken_at if v.chain_broken_at is not None else '-'}",
        f"law_ok={b(v.law_ok)}",
        f"law_excess={v.law_excess!r}",
        f"head={v.head_hash or '-'}",
        f"tenant={v.tenant or '-'}",
        f"budgets={budgets}",
        f"violations={len(v.law_violations)}",
    ]
    lines += [f"violation={m}" for m in v.law_violations]
    lines.append(f"ok={b(v.ok)}")
    return "\n".join(lines) + "\n"


def fmt_agree(a):
    lines = [
        f"agreed={b(a.agreed)}",
        f"reason={a.reason}",
        f"sender={a.sender or '-'}",
        f"receiver={a.receiver or '-'}",
        f"payload_hash={a.payload_hash or '-'}",
        f"head_a={a.head_a or '-'}",
        f"head_b={a.head_b or '-'}",
        f"entries_a={a.entries_a}",
        f"entries_b={a.entries_b}",
        f"detail={a.detail}",
    ]
    return "\n".join(lines) + "\n"


def fmt_state(agreed, refused):
    out = []
    for a in agreed:
        out.append(f"{a.xchg} agreed {a.reason} {a.payload_hash}")
    for a in refused:
        out.append(f"{a.xchg} refused {a.reason} -")
    return "\n".join(out) + ("\n" if out else "")


def fmt_anchor(v):
    lines = [
        f"status={v.status}",
        f"entries={v.entries}",
        f"pinned_entries={v.pinned_entries}",
        f"head={v.head_hash or '-'}",
        f"pinned_head={v.pinned_head or '-'}",
    ]
    return "\n".join(lines) + "\n"


# =====================================================================
# scenario2 — the Python twin of `stitchc scenario2`
# =====================================================================

TRICKY = {
    "title": "Résumé — café ☕",
    "n": 3,
    "pi": 3.5,
    "small": 1e-05,
    "i64max": 9223372036854775807,
    "i64min": -9223372036854775808,
    "u64max": 18446744073709551615,
    "big": 18446744073709551616,
    "negzero": -0.0,
    "sub": 5e-324,
    "max": 1.7976931348623157e308,
    "esc": "a\"b\\c\nd\te",
    "t": True,
    "f": False,
    "nil": None,
    "empty_obj": {},
    "empty_arr": [],
    "inner": {"arr": [1, "two", 3.25, None, [True], {"z": 0}]},
    "ключ": "cyrillic key",
    "emoji🔑": "key",
}


def build_scenario2(outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    install(1000000007)
    try:
        la = Ledger(path=str(outdir / "alice.jsonl"))
        lb = Ledger(path=str(outdir / "bob.jsonl"))
        alice = Tenant(identity="alice", budget=3.0)
        bob = Tenant(identity="bob")
        kl = KLSurprise()

        declare_budget(alice, la, budget=3.0)                       # 1
        cell_a = bind(alice, la, kind="note", payload=TRICKY)       # 2
        cell_b = bind(alice, la, kind="data",
                      payload={"v": [1, 2, 3], "ok": True})         # 3
        link(alice, la, cell_a, cell_b, "depends_on")               # 4
        assert effect(alice, la, kl, cell_a, "x",
                      predicted={}, observed={"x": 1})              # 5
        assert effect(alice, la, kl, cell_a, "x",
                      predicted={"x": 1}, observed={"x": 2})        # 6
        assert effect(alice, la, kl, cell_b, "a",
                      predicted={"a": 1, "b": 2}, observed={"a": 1})  # 7
        refused = effect(alice, la, kl, cell_b, "p",
                         predicted={}, observed={"p": 1, "q": 2})   # 8
        assert refused is False, "step 8 must be refused (gamma 4.0 > 3.0)"
        bind(bob, lb, kind="note", payload={"from": "bob"})         # 9

        p1 = {"item": "ledger", "qty": 2}
        p2 = {"item": "pen", "qty": 1}
        p3a = {"item": "ink", "qty": 1}
        p3b = {"item": "ink", "qty": 2}
        p4 = {"item": "stamp", "qty": 5}
        p5 = {"item": "seal", "qty": 1}
        book_send(alice, la, "x1", "bob", p1)                       # 10
        book_receive(bob, lb, "x1", "alice", p1)
        book_send(alice, la, "x2", "bob", p2)
        book_send(alice, la, "x3", "bob", p3a)
        book_receive(bob, lb, "x3", "alice", p3b)
        book_send(bob, lb, "x4", "alice", p4)
        book_receive(alice, la, "x4", "bob", p4)
        book_receive(bob, lb, "x5", "alice", p5)

        tick(alice, la)                                             # 11
        assert effect(alice, la, kl, cell_b, "p",
                      predicted={}, observed={"p": 1})              # 12
        attest(alice, la, cell_a, "verified")                       # 13
        delegate(alice, la, cell_a, "bob", "read")                  # 14
        contest(alice, la, cell_b, "stale")                         # 15
        merger(alice, la, cell_a, cell_b, kind="merged",
               payload={"sum": [1, 2]})                             # 16
        revoke(alice, la, cell_b, "superseded")                     # 17
        withdraw(alice, la)                                         # 18
        tick(bob, lb)                                               # 19
        la.close()
        lb.close()
    finally:
        restore()


# =====================================================================
# audit fixtures
# =====================================================================

def raw_append(path, entries):
    led = Ledger(path=path)
    for e in entries:
        led.append(dict(e))
    led.close()


def build_audit_fixtures():
    d = Path("audit")
    d.mkdir(exist_ok=True)

    # valid simple ledger via the stitch API
    p = str(d / "valid.jsonl")
    led = Ledger(path=p)
    carol = Tenant(identity="carol")
    kl = KLSurprise()
    c = bind(carol, led, kind="task", payload={"goal": "x"})
    effect(carol, led, kl, c, "s", predicted={}, observed={"s": 1})
    tick(carol, led)
    led.close()

    # tampered: flip a payload character in entry index 2
    lines = Path(p).read_text(encoding="utf-8").splitlines()
    assert '"goal":"x"' in lines[0]
    tam = list(lines)
    tam[2] = tam[2].replace('"op":"TICK"', '"op":"TICL"')
    (d / "tampered.jsonl").write_text("\n".join(tam) + "\n", encoding="utf-8")

    # recorded g does not match the replay
    raw_append(str(d / "g_mismatch.jsonl"), [
        {"t": 1, "ns": "dan", "op": "BIND", "cell": "aa", "kind": "task",
         "payload": {}, "parent": None, "g": 0.0},
        {"t": 2, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "x",
         "v": 1, "s": 1.0, "g": 0.5},
        {"t": 3, "ns": "dan", "op": "TICK", "g": 0.0},
    ])

    # plain overspend over the default budget
    raw_append(str(d / "overspend.jsonl"), [
        {"t": 1, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "x",
         "v": 1, "s": 1.0, "g": 1.0},
        {"t": 2, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "y",
         "v": 2, "s": 1.0, "g": 2.0},
    ])

    # overspend, THEN declare a bigger budget: position-aware audit
    # must still refuse (declaration cannot reach backwards)
    raw_append(str(d / "declare_late.jsonl"), [
        {"t": 1, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "x",
         "v": 1, "s": 1.0, "g": 1.0},
        {"t": 2, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "y",
         "v": 2, "s": 1.0, "g": 2.0},
        {"t": 3, "ns": "dan", "op": "BIND", "cell": "bb", "kind": "tenant",
         "payload": {"budget": 3.0}, "parent": None, "g": 2.0},
    ])

    # declare first with 3.0: the same spend is lawful
    raw_append(str(d / "declare_early.jsonl"), [
        {"t": 1, "ns": "dan", "op": "BIND", "cell": "bb", "kind": "tenant",
         "payload": {"budget": 3.0}, "parent": None, "g": 0.0},
        {"t": 2, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "x",
         "v": 1, "s": 1.0, "g": 1.0},
        {"t": 3, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "y",
         "v": 2, "s": 1.0, "g": 2.0},
    ])

    # second declaration is recorded but ignored (3.0 stays in force)
    raw_append(str(d / "declare_second_ignored.jsonl"), [
        {"t": 1, "ns": "dan", "op": "BIND", "cell": "bb", "kind": "tenant",
         "payload": {"budget": 3.0}, "parent": None, "g": 0.0},
        {"t": 2, "ns": "dan", "op": "BIND", "cell": "bc", "kind": "tenant",
         "payload": {"budget": 0.5}, "parent": None, "g": 0.0},
        {"t": 3, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "x",
         "v": 1, "s": 1.0, "g": 1.0},
        {"t": 4, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "y",
         "v": 2, "s": 1.0, "g": 2.0},
    ])

    # two tenants in one file -> tenant verdict is None
    raw_append(str(d / "multi_tenant.jsonl"), [
        {"t": 1, "ns": "alice", "op": "TICK", "g": 0.0},
        {"t": 2, "ns": "bob", "op": "TICK", "g": 0.0},
    ])

    # integer (not float) s and g fields
    raw_append(str(d / "int_fields.jsonl"), [
        {"t": 1, "ns": "dan", "op": "EFFECT", "cell": "aa", "k": "x",
         "v": 1, "s": 1, "g": 1},
    ])

    # NaN in a payload: Python append writes it; both audits must
    # still verify the chain (readers are unpoliced)
    led = Ledger(path=str(d / "nan_payload.jsonl"))
    led.append({"t": 1, "ns": "dan", "op": "BIND", "cell": "aa",
                "kind": "task", "payload": {"x": float("nan")},
                "parent": None, "g": 0.0})
    led.append({"t": 2, "ns": "dan", "op": "TICK", "g": 0.0})
    led.close()

    # blank lines and padded lines interleaved (chain must verify)
    body = Path(p).read_text(encoding="utf-8").splitlines()
    padded = ["", body[0], "   " + body[1] + "  ", "", body[2], ""]
    (d / "blank_lines.jsonl").write_text("\n".join(padded) + "\n",
                                         encoding="utf-8")

    # duplicate keys in a raw line: Python keeps the last; the hash
    # is computed over the deduplicated canonical form
    entry = {"t": 1, "ns": "dan", "op": "TICK", "g": 0.0}
    canon = json.dumps(entry, separators=(",", ":"), sort_keys=True,
                       ensure_ascii=False)
    h = Ledger._fnv1a(canon, FNV_OFFSET)
    raw_line = ('{"t":1,"ns":"dan","op":"TICK","g":9.9,"g":0.0,'
                f'"h":"{h:016x}"}}')
    (d / "dup_keys.jsonl").write_text(raw_line + "\n", encoding="utf-8")

    # empty file
    (d / "empty.jsonl").write_text("", encoding="utf-8")

    for f in sorted(d.glob("*.jsonl")):
        v = audit_ledger(str(f))
        (d / (f.stem + ".expected")).write_text(fmt_audit(v),
                                                encoding="utf-8")


# =====================================================================
# agree pair fixtures
# =====================================================================

def build_pair(name, build_fn):
    d = Path("pairs") / name
    d.mkdir(parents=True, exist_ok=True)
    pa, pb = str(d / "a.jsonl"), str(d / "b.jsonl")
    xchgs = build_fn(pa, pb)
    for x in xchgs:
        v = agree(pa, pb, x)
        (d / f"agree-{x}.expected").write_text(fmt_agree(v), encoding="utf-8")
        v2 = agree(pb, pa, x)
        (d / f"agree-{x}.rev.expected").write_text(fmt_agree(v2),
                                                   encoding="utf-8")
    ag, rf = agreed_state(pa, pb)
    (d / "state.expected").write_text(fmt_state(ag, rf), encoding="utf-8")


def pair_mint(pa, pb):
    la, lb = Ledger(path=pa), Ledger(path=pb)
    fred, gina = Tenant(identity="fred"), Tenant(identity="gina")
    book_send(fred, la, "deal", "gina", {"amt": 7})
    book_receive(gina, lb, "deal", "fred", {"amt": 7})
    la.close(); lb.close()
    return ["deal", "ghost"]


def pair_peer_mismatch(pa, pb):
    la, lb = Ledger(path=pa), Ledger(path=pb)
    fred, gina = Tenant(identity="fred"), Tenant(identity="gina")
    book_send(fred, la, "deal", "gina", {"amt": 7})
    book_receive(gina, lb, "deal", "henry", {"amt": 7})
    la.close(); lb.close()
    return ["deal"]


def pair_chain_broken(pa, pb):
    la, lb = Ledger(path=pa), Ledger(path=pb)
    fred, gina = Tenant(identity="fred"), Tenant(identity="gina")
    book_send(fred, la, "deal", "gina", {"amt": 7})
    book_receive(gina, lb, "deal", "fred", {"amt": 7})
    la.close(); lb.close()
    lines = Path(pb).read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"amt":7', '"amt":8')
    Path(pb).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ["deal"]


def pair_law_violated(pa, pb):
    la, lb = Ledger(path=pa), Ledger(path=pb)
    fred, gina = Tenant(identity="fred"), Tenant(identity="gina")
    book_send(fred, la, "deal", "gina", {"amt": 7})
    # fred overspends afterwards via raw appends (chain stays valid)
    la.append({"t": 99, "ns": "fred", "op": "EFFECT", "cell": "aa",
               "k": "x", "v": 1, "s": 1.0, "g": 1.0})
    la.append({"t": 100, "ns": "fred", "op": "EFFECT", "cell": "aa",
               "k": "y", "v": 2, "s": 1.0, "g": 2.0})
    book_receive(gina, lb, "deal", "fred", {"amt": 7})
    la.close(); lb.close()
    return ["deal"]


# =====================================================================
# anchor fixtures
# =====================================================================

def build_anchors():
    d = Path("anchors")
    d.mkdir(exist_ok=True)
    src = Path("rich/alice.jsonl")
    pin = agree("rich/alice.jsonl", "rich/bob.jsonl", "x1")
    assert pin.agreed
    head, count = pin.head_a, pin.entries_a

    def case(fname, path_arg, head_arg, count_arg, label):
        v = verify_anchor(path_arg, head_arg, count_arg)
        (d / (label + ".expected")).write_text(fmt_anchor(v),
                                               encoding="utf-8")
        (d / (label + ".args")).write_text(
            f"{fname} {head_arg or '-'} {count_arg}\n", encoding="utf-8")

    (d / "orig.jsonl").write_bytes(src.read_bytes())
    case("anchors/orig.jsonl", "anchors/orig.jsonl", head, count, "anchored")

    lines = src.read_text(encoding="utf-8").splitlines()
    (d / "truncated.jsonl").write_text("\n".join(lines[:-2]) + "\n",
                                       encoding="utf-8")
    case("anchors/truncated.jsonl", "anchors/truncated.jsonl", head, count,
         "truncated")

    # rewrite history but repair the chain: only the pin catches it
    entries = [json.loads(x) for x in lines]
    entries[1]["payload"]["n"] = 999
    last = FNV_OFFSET
    out = []
    for e in entries:
        e.pop("h", None)
        canon = json.dumps(e, separators=(",", ":"), sort_keys=True,
                           ensure_ascii=False)
        last = Ledger._fnv1a(canon, last)
        e["h"] = f"{last:016x}"
        out.append(json.dumps(e, separators=(",", ":"), sort_keys=True,
                              ensure_ascii=False))
    (d / "rewritten.jsonl").write_text("\n".join(out) + "\n",
                                       encoding="utf-8")
    case("anchors/rewritten.jsonl", "anchors/rewritten.jsonl", head, count,
         "rewritten")

    # tamper inside the pinned prefix without repairing: chain_broken
    tam = list(lines)
    tam[0] = tam[0].replace('"budget":3.0', '"budget":4.0')
    (d / "broken.jsonl").write_text("\n".join(tam) + "\n", encoding="utf-8")
    case("anchors/broken.jsonl", "anchors/broken.jsonl", head, count,
         "chain_broken")

    case("anchors/nope.jsonl", "anchors/nope.jsonl", head, count, "missing")

    v = verify_anchor("anchors/orig.jsonl", None, 0)
    (d / "zero.expected").write_text(fmt_anchor(v), encoding="utf-8")
    (d / "zero.args").write_text("anchors/orig.jsonl - 0\n", encoding="utf-8")


# =====================================================================
# float / canon / phash corpora
# =====================================================================

def build_corpora():
    rng = random.Random(20261029)
    pats = [0, 1, 0x8000000000000000, 0x7FF0000000000000,
            0xFFF0000000000000, 0x7FF8000000000000, 0x0000000000000001,
            0x000FFFFFFFFFFFFF, 0x7FEFFFFFFFFFFFFF, 0x3FF0000000000000]
    pats += [rng.getrandbits(64) for _ in range(200000)]
    for k in range(-323, 309):
        pats.append(struct.unpack(">Q", struct.pack(">d", 10.0 ** k))[0])
    for k in range(-1074, 1024, 7):
        pats.append(struct.unpack(">Q", struct.pack(">d", 2.0 ** k))[0])
    with open("floats.txt", "w") as f:
        for p in pats:
            f.write(f"{p:016x}\n")
    with open("floats.expected", "w") as f:
        for p in pats:
            d = struct.unpack(">d", struct.pack(">Q", p))[0]
            f.write(json.dumps(d) + "\n")

    vals = [
        TRICKY,
        {"a": [1e-05, 1e-06, 1.5e-07, 9.999e-05, 123456789.12345679,
               0.1, -0.0, 5e-324, 1.7976931348623157e308, 1e16, 1e15,
               1234567890123456.0, 0.0001, 0.00001]},
        {"ints": [0, -1, 9223372036854775807, -9223372036854775808,
                  9223372036854775808, 18446744073709551615,
                  18446744073709551616, -18446744073709551617,
                  10 ** 30, -(10 ** 30)]},
        {"s": "líne\n\t\"q\" \\  \U0001F600 \u2028 \u00e9\u0301"},
        {"nested": {"x": [{"y": []}, {}], "z": {"": ""}}},
        [3.141592653589793, "π", {"ключ": [True, False, None]}],
        "plain string",
        42,
        4.5,
        None,
        True,
        {"unicode_keys": {"\u00e9": 1, "e\u0301": 2, "\U0001F600": 3,
                          "\u4e2d\u6587": 4, " ": 5, "": 6}},
    ]
    with open("canon.txt", "w") as f:
        for v in vals:
            f.write(json.dumps(v) + "\n")
    with open("canon.expected", "w") as f:
        for v in vals:
            f.write(json.dumps(v, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False) + "\n")
    with open("phash.txt", "w") as f:
        for v in vals:
            f.write(json.dumps(v) + "\n")
    with open("phash.expected", "w") as f:
        for v in vals:
            f.write(payload_hash(v) + "\n")


def main():
    build_scenario2("scenario2-py")
    # the rich pair is the scenario2 pair under a stable name
    import shutil
    shutil.rmtree("rich", ignore_errors=True)
    shutil.copytree("scenario2-py", "rich")
    v = fmt_state(*agreed_state("rich/alice.jsonl", "rich/bob.jsonl"))
    Path("rich/state.expected").write_text(v, encoding="utf-8")
    for x in ["x1", "x2", "x3", "x4", "x5", "never"]:
        a = agree("rich/alice.jsonl", "rich/bob.jsonl", x)
        Path(f"rich/agree-{x}.expected").write_text(fmt_agree(a),
                                                    encoding="utf-8")
        a2 = agree("rich/bob.jsonl", "rich/alice.jsonl", x)
        Path(f"rich/agree-{x}.rev.expected").write_text(fmt_agree(a2),
                                                        encoding="utf-8")
    for nm in ["alice", "bob"]:
        av = audit_ledger(f"rich/{nm}.jsonl")
        Path(f"rich/audit-{nm}.expected").write_text(fmt_audit(av),
                                                     encoding="utf-8")

    build_audit_fixtures()
    build_pair("mint", pair_mint)
    build_pair("peer_mismatch", pair_peer_mismatch)
    build_pair("chain_broken", pair_chain_broken)
    build_pair("law_violated", pair_law_violated)
    build_anchors()
    build_corpora()
    print("fixtures generated under", FX)


if __name__ == "__main__":
    main()
