"""
Tests for v0.3 N-party agreement (agree_n / agreed_state_n).

Closes the v0.2 known limitation: "Bilateral only. Minting is defined
for two ledgers. N-party agreement is pairwise folds, by design — no
group primitive was added."

v0.3 design: the bilateral rule generalises cleanly. Every SEND booked
in any ledger must have a mirror RECEIVE booked by the named peer in
some OTHER ledger, all bookings agree on the payload hash, and every
ledger has at least one booking. For N=2 this is exactly `agree` (a
2-cycle); for N>2 a typical ring has each party booking a SEND to
their successor and a RECEIVE from their predecessor.
"""

import pytest
from pathlib import Path

import stitch
from stitch import (
    Tenant, Ledger,
    book_send, book_receive, declare_budget,
    agree, agree_n, agreed_state_n,
    LOG2_3,
)


# ============================================================================
# 2-party: agree_n should match agree for the bilateral case
# ============================================================================

def test_agree_n_reduces_to_bilateral_for_n2(tmp_path):
    """For N=2, agree_n should produce the same verdict as agree."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    payload = {"doc": "exchange-1"}
    book_send(alice, la, xchg="x1", peer="bob", payload=payload)
    book_receive(bob, lb, xchg="x1", peer="alice", payload=payload)
    la.close()
    lb.close()

    v_bilateral = agree(str(pa), str(pb), "x1")
    v_n = agree_n([str(pa), str(pb)], "x1")

    assert v_n.agreed == v_bilateral.agreed
    assert v_n.reason == v_bilateral.reason
    assert v_n.payload_hash == v_bilateral.payload_hash
    assert v_n.parties == ["alice", "bob"]
    assert v_n.heads == [v_bilateral.head_a, v_bilateral.head_b]
    assert v_n.entries_list == [v_bilateral.entries_a, v_bilateral.entries_b]


# ============================================================================
# 3-party ring (the canonical N>2 case)
# ============================================================================

def test_agree_n_3_party_ring_mints(tmp_path):
    """A 3-party ring: A→B, B→C, C→A. Each party books SEND to successor + RECEIVE from predecessor."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    pc = tmp_path / "carol.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    lc = Ledger(path=str(pc))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    carol = Tenant(identity="carol")

    payload = {"doc": "ring-exchange", "value": 100}
    # Alice: SEND to Bob, RECEIVE from Carol
    book_send(alice, la, xchg="ring1", peer="bob", payload=payload)
    book_receive(alice, la, xchg="ring1", peer="carol", payload=payload)
    # Bob: SEND to Carol, RECEIVE from Alice
    book_send(bob, lb, xchg="ring1", peer="carol", payload=payload)
    book_receive(bob, lb, xchg="ring1", peer="alice", payload=payload)
    # Carol: SEND to Alice, RECEIVE from Bob
    book_send(carol, lc, xchg="ring1", peer="alice", payload=payload)
    book_receive(carol, lc, xchg="ring1", peer="bob", payload=payload)
    la.close()
    lb.close()
    lc.close()

    v = agree_n([str(pa), str(pb), str(pc)], "ring1")
    assert v.agreed, f"3-party ring should mint: {v.reason} {v.detail}"
    assert v.parties == ["alice", "bob", "carol"]
    assert v.payload_hash == stitch.payload_hash(payload)
    assert len(v.heads) == 3
    assert v.entries_list == [2, 2, 2]  # 2 bookings per ledger


def test_agree_n_3_party_ring_with_missing_send_refuses(tmp_path):
    """A ring where Bob's SEND has no mirror RECEIVE in Carol's ledger."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    pc = tmp_path / "carol.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    lc = Ledger(path=str(pc))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    carol = Tenant(identity="carol")

    payload = {"doc": "broken-ring"}
    # Alice books full ring side
    book_send(alice, la, xchg="ring1", peer="bob", payload=payload)
    book_receive(alice, la, xchg="ring1", peer="carol", payload=payload)
    # Bob books SEND to Carol, but RECEIVE from a stranger (not Alice)
    book_send(bob, lb, xchg="ring1", peer="carol", payload=payload)
    book_receive(bob, lb, xchg="ring1", peer="stranger", payload=payload)  # not Alice
    # Carol books RECEIVE from Bob, but no SEND to Alice
    book_receive(carol, lc, xchg="ring1", peer="bob", payload=payload)
    la.close()
    lb.close()
    lc.close()

    v = agree_n([str(pa), str(pb), str(pc)], "ring1")
    assert not v.agreed
    # Alice's SEND to Bob — mirror is Bob RECEIVE from Alice. Bob booked RECEIVE from stranger, not alice → MISSING_MIRROR
    assert v.reason == stitch.MISSING_MIRROR, (
        f"Expected MISSING_MIRROR, got {v.reason}: {v.detail}"
    )


def test_agree_n_3_party_ring_broken_send_without_mirror(tmp_path):
    """A ring where Bob's SEND has no mirror RECEIVE in Carol's ledger."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    pc = tmp_path / "carol.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    lc = Ledger(path=str(pc))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    carol = Tenant(identity="carol")

    payload = {"doc": "broken-ring"}
    # Alice books full ring side
    book_send(alice, la, xchg="ring1", peer="bob", payload=payload)
    book_receive(alice, la, xchg="ring1", peer="carol", payload=payload)
    # Bob books SEND to Carol, but RECEIVE from a stranger (not Alice)
    book_send(bob, lb, xchg="ring1", peer="carol", payload=payload)
    book_receive(bob, lb, xchg="ring1", peer="stranger", payload=payload)  # not Alice
    # Carol books RECEIVE from Bob, but no SEND to Alice
    book_receive(carol, lc, xchg="ring1", peer="bob", payload=payload)
    la.close()
    lb.close()
    lc.close()

    v = agree_n([str(pa), str(pb), str(pc)], "ring1")
    assert not v.agreed
    # Alice's SEND to Bob — mirror is Bob RECEIVE from Alice. Bob booked RECEIVE from stranger, not alice → MISSING_MIRROR
    assert v.reason == stitch.MISSING_MIRROR, (
        f"Expected MISSING_MIRROR, got {v.reason}: {v.detail}"
    )


# ============================================================================
# Refusals
# ============================================================================

def test_agree_n_refuses_when_one_ledger_has_no_booking(tmp_path):
    """If one of N ledgers has no booking for xchg, refuse with MISSING_MIRROR."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    pc = tmp_path / "carol.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    lc = Ledger(path=str(pc))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    carol = Tenant(identity="carol")

    payload = {"doc": "x1"}
    # Alice and Bob book an exchange; Carol's ledger is empty for x1
    book_send(alice, la, xchg="x1", peer="bob", payload=payload)
    book_receive(bob, lb, xchg="x1", peer="alice", payload=payload)
    # Carol books something else
    book_send(carol, lc, xchg="x_other", peer="alice", payload={"doc": "different"})
    la.close()
    lb.close()
    lc.close()

    v = agree_n([str(pa), str(pb), str(pc)], "x1")
    assert not v.agreed
    assert v.reason == stitch.MISSING_MIRROR
    assert "carol" in v.detail or "ledger" in v.detail.lower()


def test_agree_n_refuses_on_payload_mismatch(tmp_path):
    """If two parties book different payload hashes for the same xchg, refuse."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    pc = tmp_path / "carol.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    lc = Ledger(path=str(pc))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    carol = Tenant(identity="carol")

    # Alice and Bob book payload A; Carol books payload B for the same xchg
    payload_a = {"doc": "version-A"}
    payload_b = {"doc": "version-B"}  # different
    book_send(alice, la, xchg="x1", peer="bob", payload=payload_a)
    book_receive(bob, lb, xchg="x1", peer="alice", payload=payload_a)
    book_send(carol, lc, xchg="x1", peer="alice", payload=payload_b)  # different!
    la.close()
    lb.close()
    lc.close()

    v = agree_n([str(pa), str(pb), str(pc)], "x1")
    assert not v.agreed
    assert v.reason == stitch.PAYLOAD_MISMATCH


def test_agree_n_refuses_on_chain_break(tmp_path):
    """If any one ledger's chain is broken, refuse with CHAIN_BROKEN."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    pc = tmp_path / "carol.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    lc = Ledger(path=str(pc))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    carol = Tenant(identity="carol")

    payload = {"doc": "x1"}
    book_send(alice, la, xchg="x1", peer="bob", payload=payload)
    book_receive(bob, lb, xchg="x1", peer="alice", payload=payload)
    book_send(carol, lc, xchg="x1", peer="alice", payload=payload)
    la.close()
    lb.close()
    lc.close()

    # Tamper with Bob's ledger (change a single char)
    content = pb.read_bytes()
    tampered = content.replace(b"alice", b"al ice", 1)  # break a hash
    pb.write_bytes(tampered)

    v = agree_n([str(pa), str(pb), str(pc)], "x1")
    assert not v.agreed
    assert v.reason == stitch.CHAIN_BROKEN


# ============================================================================
# agreed_state_n: full fold across N ledgers
# ============================================================================

def test_agreed_state_n_folds_3_party_ledgers(tmp_path):
    """agreed_state_n folds multiple exchanges across 3 ledgers."""
    paths = []
    tenants = ["alice", "bob", "carol"]
    ledgers = []
    for name in tenants:
        p = tmp_path / f"{name}.jsonl"
        paths.append(str(p))
        ledgers.append(Ledger(path=str(p)))

    alice_t = Tenant(identity="alice")
    bob_t = Tenant(identity="bob")
    carol_t = Tenant(identity="carol")

    # Two exchanges: x1 (full ring, should mint) and x2 (broken, should refuse)
    p1 = {"doc": "ring1"}
    # x1: full ring
    book_send(alice_t, ledgers[0], xchg="x1", peer="bob", payload=p1)
    book_receive(alice_t, ledgers[0], xchg="x1", peer="carol", payload=p1)
    book_send(bob_t, ledgers[1], xchg="x1", peer="carol", payload=p1)
    book_receive(bob_t, ledgers[1], xchg="x1", peer="alice", payload=p1)
    book_send(carol_t, ledgers[2], xchg="x1", peer="alice", payload=p1)
    book_receive(carol_t, ledgers[2], xchg="x1", peer="bob", payload=p1)

    # x2: only Alice books (Bob and Carol don't) → MISSING_MIRROR
    book_send(alice_t, ledgers[0], xchg="x2", peer="bob", payload={"doc": "x2"})

    for l in ledgers:
        l.close()

    agreed, refused = agreed_state_n(paths)
    assert len(agreed) == 1
    assert len(refused) == 1
    assert agreed[0].xchg == "x1"
    assert agreed[0].agreed
    assert refused[0].xchg == "x2"
    assert not refused[0].agreed


# ============================================================================
# 4-party ring (generalisation check)
# ============================================================================

def test_agree_n_4_party_ring_mints(tmp_path):
    """A 4-party ring: A→B→C→D→A. Confirms the rule generalises past N=3."""
    paths = []
    tenants = ["alice", "bob", "carol", "dave"]
    ledgers = []
    for name in tenants:
        p = tmp_path / f"{name}.jsonl"
        paths.append(str(p))
        ledgers.append(Ledger(path=str(p)))

    tenant_objs = [Tenant(identity=n) for n in tenants]
    payload = {"doc": "4-party-ring"}
    n = len(tenants)
    # Each party books SEND to next and RECEIVE from previous
    for i, t in enumerate(tenant_objs):
        next_party = tenants[(i + 1) % n]
        prev_party = tenants[(i - 1) % n]
        book_send(t, ledgers[i], xchg="ring4", peer=next_party, payload=payload)
        book_receive(t, ledgers[i], xchg="ring4", peer=prev_party, payload=payload)

    for l in ledgers:
        l.close()

    v = agree_n(paths, "ring4")
    assert v.agreed, f"4-party ring should mint: {v.reason} {v.detail}"
    assert v.parties == sorted(tenants)
    assert len(v.heads) == 4
    assert v.entries_list == [2, 2, 2, 2]
