"""
stitch v0.2 tests — the agreement half: foreign-ledger audit, bilateral
minting, and the head-hash anchor.

Run: python -m pytest tests/ -v
"""

import json
import time

import pytest

import stitch
from stitch import (
    Tenant,
    Ledger,
    LOG2_3,
    audit_ledger,
    agree,
    agreed_state,
    verify_anchor,
    book_send,
    book_receive,
    payload_hash,
)

PAYLOAD = {"doc": "harbor-chart", "rev": 3}


def _pair(tmp_path):
    """Fresh alice/bob tenants + ledgers; returns (alice, bob, la, lb, pa, pb)."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    alice, bob = Tenant("alice"), Tenant("bob")
    la, lb = Ledger(str(pa)), Ledger(str(pb))
    return alice, bob, la, lb, pa, pb


def _booked_pair(tmp_path):
    """A clean mirrored exchange x1 between alice and bob."""
    alice, bob, la, lb, pa, pb = _pair(tmp_path)
    book_send(alice, la, "x1", "bob", PAYLOAD)
    book_receive(bob, lb, "x1", "alice", PAYLOAD)
    la.close()
    lb.close()
    return pa, pb


# ============================================================================
# Booking helpers + payload hash
# ============================================================================

def test_payload_hash_is_canonical():
    assert payload_hash({"b": 1, "a": 2}) == payload_hash({"a": 2, "b": 1})
    assert payload_hash({"a": 2}) != payload_hash({"a": 3})
    assert len(payload_hash(PAYLOAD)) == 64


def test_booking_uses_bind_with_exchange_kind(tmp_path):
    alice, bob, la, lb, pa, pb = _pair(tmp_path)
    cell = book_send(alice, la, "x1", "bob", PAYLOAD)
    la.close()
    lb.close()
    assert cell.kind == "exchange"
    entries = [json.loads(l) for l in pa.read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]["op"] == "BIND"
    assert entries[0]["payload"] == {
        "xchg": "x1", "side": "send", "peer": "bob",
        "ph": payload_hash(PAYLOAD),
    }


# ============================================================================
# Audit
# ============================================================================

def test_audit_clean_ledger(tmp_path):
    pa, _ = _booked_pair(tmp_path)
    v = audit_ledger(pa)
    assert v.ok and v.chain_ok and v.law_ok
    assert v.entries == 1
    assert v.tenant == "alice"
    last = json.loads(pa.read_text().splitlines()[-1])
    assert v.head_hash == last["h"]
    assert v.law_excess == 0.0
    assert v.budget == LOG2_3


def test_audit_forged_law_ledger(tmp_path):
    # Chain-valid raw appends claiming s=2.5 in one tick (the v0.1 forgery).
    p = tmp_path / "mallory.jsonl"
    ledger = Ledger(str(p))
    t0 = time.time_ns()
    ledger.append({"t": t0, "ns": "mallory", "op": "BIND", "cell": "c0ffee",
                   "kind": "task", "payload": {}, "parent": None, "g": 0.0})
    ledger.append({"t": t0 + 1, "ns": "mallory", "op": "EFFECT",
                   "cell": "c0ffee", "k": "status", "v": "done",
                   "s": 2.5, "g": 2.5})
    ledger.close()
    v = audit_ledger(p)
    assert v.chain_ok                      # the chain alone waves it through
    assert not v.law_ok
    assert v.law_excess == pytest.approx(2.5 - LOG2_3)
    assert v.entries == 2


def _tamper_ph(path):
    """Flip one hex char of the recorded payload hash — history rewritten,
    hashes untouched, exactly the v0.1 tamper shape."""
    text = path.read_text()
    ph = payload_hash(PAYLOAD)
    assert ph in text
    flipped = ("0" if ph[0] != "0" else "1") + ph[1:]
    path.write_text(text.replace(ph, flipped, 1))


def test_audit_tampered_ledger(tmp_path):
    pa, _ = _booked_pair(tmp_path)
    _tamper_ph(pa)
    v = audit_ledger(pa)
    assert not v.chain_ok
    assert v.chain_broken_at == 0
    assert v.entries == 0
    assert v.head_hash is None


# ============================================================================
# Agreement / minting
# ============================================================================

def test_clean_bilateral_agreement_mints(tmp_path):
    pa, pb = _booked_pair(tmp_path)
    a = agree(pa, pb, "x1")
    assert a.agreed and a.reason == stitch.AGREED
    assert a.sender == "alice" and a.receiver == "bob"
    assert a.payload_hash == payload_hash(PAYLOAD)
    # The agreement pins both heads at agreement time.
    assert a.head_a == audit_ledger(pa).head_hash
    assert a.head_b == audit_ledger(pb).head_hash
    assert a.entries_a == 1 and a.entries_b == 1
    # Argument order does not matter.
    assert agree(pb, pa, "x1").agreed
    # And the fold sees it as shared state.
    agreed, refused = agreed_state(pa, pb)
    assert [v.xchg for v in agreed] == ["x1"]
    assert refused == []


def test_no_send_is_distinct_from_missing_mirror(tmp_path):
    alice, bob, la, lb, pa, pb = _pair(tmp_path)
    la.close()
    lb.close()
    a = agree(pa, pb, "never-booked")
    assert not a.agreed and a.reason == stitch.NO_SEND


def test_one_sided_entry_does_not_mint(tmp_path):
    alice, bob, la, lb, pa, pb = _pair(tmp_path)
    book_send(alice, la, "x1", "bob", PAYLOAD)
    la.close()
    lb.close()
    a = agree(pa, pb, "x1")
    assert not a.agreed and a.reason == stitch.MISSING_MIRROR
    agreed, refused = agreed_state(pa, pb)
    assert agreed == []
    assert [v.reason for v in refused] == [stitch.MISSING_MIRROR]


def test_payload_mismatch_does_not_mint(tmp_path):
    alice, bob, la, lb, pa, pb = _pair(tmp_path)
    book_send(alice, la, "x1", "bob", PAYLOAD)
    book_receive(bob, lb, "x1", "alice", {"doc": "harbor-chart", "rev": 4})
    la.close()
    lb.close()
    a = agree(pa, pb, "x1")
    assert not a.agreed and a.reason == stitch.PAYLOAD_MISMATCH


def test_peer_mismatch_does_not_mint(tmp_path):
    alice, bob, la, lb, pa, pb = _pair(tmp_path)
    book_send(alice, la, "x1", "bob", PAYLOAD)
    # Bob books a receive for x1, but names carol as the sender.
    book_receive(bob, lb, "x1", "carol", PAYLOAD)
    la.close()
    lb.close()
    a = agree(pa, pb, "x1")
    assert not a.agreed and a.reason == stitch.PEER_MISMATCH


def test_forged_law_ledger_rejected_by_agree(tmp_path):
    pa, pb = _booked_pair(tmp_path)
    # Bob's writer goes rogue after booking: a raw, chain-valid append
    # claiming 2.5 bits of surprise in the current tick.
    ledger = Ledger(str(pb))
    ledger.append({"t": time.time_ns(), "ns": "bob", "op": "EFFECT",
                   "cell": "c0ffee", "k": "status", "v": "done",
                   "s": 2.5, "g": 2.5})
    ledger.close()
    a = agree(pa, pb, "x1")
    assert not a.agreed and a.reason == stitch.LAW_VIOLATED


def test_tampered_ledger_rejected_by_agree(tmp_path):
    pa, pb = _booked_pair(tmp_path)
    _tamper_ph(pb)
    a = agree(pa, pb, "x1")
    assert not a.agreed and a.reason == stitch.CHAIN_BROKEN
    assert "entry 0" in a.detail


# ============================================================================
# Head-hash anchor
# ============================================================================

def test_tail_truncation_caught_by_anchor(tmp_path):
    alice, bob, la, lb, pa, pb = _pair(tmp_path)
    book_send(alice, la, "x1", "bob", PAYLOAD)
    book_receive(bob, lb, "x1", "alice", PAYLOAD)
    stitch.tick(bob, lb)  # bob's ledger: booking + tick (2 entries)
    la.close()
    lb.close()
    a = agree(pa, pb, "x1")
    assert a.agreed and a.entries_b == 2

    # Cut bob's tail: the TICK entry disappears after agreement.
    lines = pb.read_text().splitlines(keepends=True)
    pb.write_text("".join(lines[:-1]))

    v = verify_anchor(pb, a.head_b, a.entries_b)
    assert v.status == "truncated"
    assert v.entries == 1
    # The mirror booking survives, so a *fresh* agreement still mints —
    # but under a different head. That is exactly what the pin is for:
    # without it, the truncation is invisible.
    fresh = agree(pa, pb, "x1")
    assert fresh.agreed and fresh.head_b != a.head_b


def test_anchor_holds_when_ledger_only_grows(tmp_path):
    pa, pb = _booked_pair(tmp_path)
    a = agree(pa, pb, "x1")
    assert a.agreed
    bob = Tenant("bob")
    lb = Ledger(str(pb))
    stitch.tick(bob, lb)
    lb.close()
    v = verify_anchor(pb, a.head_b, a.entries_b)
    assert v.status == "anchored"
    assert v.entries == 2


def test_anchor_detects_rewrite_and_missing(tmp_path):
    pa, pb = _booked_pair(tmp_path)
    a = agree(pa, pb, "x1")
    assert a.agreed
    # Rewrite history with a different ledger under the same path.
    pc = tmp_path / "carol.jsonl"
    carol = Tenant("carol")
    lc = Ledger(str(pc))
    book_send(carol, lc, "x9", "dave", PAYLOAD)
    lc.close()
    v = verify_anchor(pc, a.head_b, a.entries_b)
    assert v.status == "rewritten"
    v = verify_anchor(tmp_path / "gone.jsonl", a.head_b, a.entries_b)
    assert v.status == "missing"
