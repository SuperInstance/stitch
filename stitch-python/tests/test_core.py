"""
stitch test suite — tests for the core data structures and the 11 stitches.

Run: python -m pytest tests/ -v
"""

import json
import os
import tempfile
import shutil
import pytest

import stitch
from stitch import (
    Tenant,
    Cell,
    Ledger,
    KLSurprise,
    LOG2_3,
    bind,
    link,
    effect,
    view,
    tick,
    attest,
    delegate,
    contest,
    merger,
    revoke,
    withdraw,
)
from stitch.exceptions import EffectRejected


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tmp_ledger(tmp_path):
    """A fresh Ledger in a temp directory."""
    path = tmp_path / "state.jsonl"
    return Ledger(path=str(path))


@pytest.fixture
def alice():
    """A default tenant with the log₂3 budget."""
    return Tenant(identity="alice")


@pytest.fixture
def surprise():
    return KLSurprise()


# ============================================================================
# Constants
# ============================================================================

def test_constants():
    assert LOG2_3 == 1.584962500721156
    assert stitch.FNV_OFFSET == 0xcbf29ce484222325
    assert stitch.FNV_PRIME == 0x100000001b3
    assert stitch.MASK_64 == 0xFFFFFFFFFFFFFFFF


# ============================================================================
# Tenant
# ============================================================================

def test_tenant_default_budget():
    t = Tenant(identity="alice")
    assert t.budget == LOG2_3
    assert t.gamma == 0.0
    assert t.eta == LOG2_3
    assert t.pending == 0.0


def test_tenant_custom_budget():
    t = Tenant(identity="alice", budget=3.0)
    assert t.budget == 3.0
    assert t.eta == 3.0


def test_tenant_charge_and_afford():
    t = Tenant(identity="alice")
    assert t.can_afford(1.0)
    assert t.charge(1.0)
    assert t.gamma == 1.0
    assert t.eta == pytest.approx(LOG2_3 - 1.0)
    # Charging too much should fail
    too_much = LOG2_3 + 0.1
    assert not t.can_afford(too_much)
    assert not t.charge(too_much)
    assert t.gamma == 1.0  # unchanged


def test_tenant_tick_resets_gamma():
    t = Tenant(identity="alice")
    t.charge(1.0)
    assert t.gamma == 1.0
    t.reset_tick()
    assert t.gamma == 0.0
    assert t.eta == LOG2_3


def test_tenant_pre_charge_commit_and_refund():
    t = Tenant(identity="alice")
    # Pre-charge 0.5
    assert t.pre_charge(0.5)
    assert t.pending == 0.5
    assert t.eta == pytest.approx(LOG2_3 - 0.5)
    # Pre-charge too much should fail
    too_much = LOG2_3  # would push pending+gamma+pending past budget
    assert not t.pre_charge(too_much)
    # Refund
    t.refund_pending()
    assert t.pending == 0.0
    assert t.eta == LOG2_3
    # Pre-charge and commit
    t.pre_charge(0.5)
    t.commit_pending(0.3)  # actual was less than pre-charge
    assert t.gamma == 0.3
    assert t.pending == 0.0


# ============================================================================
# Surprise oracle
# ============================================================================

def test_kl_surprise_identical():
    s = KLSurprise()
    assert s.compute({"a": 1}, {"a": 1}) == 0.0


def test_kl_surprise_mismatched_value():
    s = KLSurprise()
    assert s.compute({"a": 1}, {"a": 2}) == 1.0


def test_kl_surprise_new_key():
    s = KLSurprise()
    assert s.compute({"a": 1}, {"a": 1, "b": 2}) == 1.0


def test_kl_surprise_over_complete_prediction():
    """Predicted keys not in observed don't add surprise."""
    s = KLSurprise()
    assert s.compute({"a": 1, "b": 2}, {"a": 1}) == 0.0


def test_kl_surprise_multiple_mismatches():
    s = KLSurprise()
    assert s.compute({"a": 1, "b": 2}, {"a": 5, "b": 6, "c": 7}) == 3.0


def test_kl_surprise_empty():
    s = KLSurprise()
    assert s.compute({}, {}) == 0.0


# ============================================================================
# Ledger
# ============================================================================

def test_ledger_append_and_verify(tmp_ledger):
    ledger = tmp_ledger
    # Append a few entries
    h1 = ledger.append({"t": 1, "ns": "alice", "op": "BIND", "cell": "a", "g": 0.0})
    h2 = ledger.append({"t": 2, "ns": "alice", "op": "TICK", "g": 0.0})
    h3 = ledger.append({"t": 3, "ns": "alice", "op": "BIND", "cell": "b", "g": 0.0})
    # Hashes should be different
    assert h1 != h2 != h3
    # Hash chain should verify
    assert ledger.verify()


def test_ledger_tamper_evident(tmp_ledger, tmp_path):
    ledger = tmp_ledger
    ledger.append({"t": 1, "ns": "alice", "op": "BIND", "cell": "a", "g": 0.0})
    ledger.append({"t": 2, "ns": "alice", "op": "TICK", "g": 0.0})
    ledger.close()
    # Tamper with the file
    path = tmp_path / "state.jsonl"
    content = path.read_text()
    # Change "alice" to "mallory" in the first entry
    tampered = content.replace('"alice"', '"mallory"', 1)
    path.write_text(tampered)
    # Re-open and verify
    ledger2 = Ledger(path=str(path))
    assert not ledger2.verify()


def test_ledger_read_all(tmp_ledger):
    ledger = tmp_ledger
    ledger.append({"t": 1, "ns": "alice", "op": "BIND", "cell": "a", "g": 0.0})
    ledger.append({"t": 2, "ns": "alice", "op": "TICK", "g": 0.0})
    ledger.close()
    entries = list(ledger.read_all())
    assert len(entries) == 2
    assert entries[0]["op"] == "BIND"
    assert entries[1]["op"] == "TICK"


def test_ledger_byte_exactness(tmp_path):
    """Two ledgers with identical inputs produce identical output bytes."""
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "b.jsonl"
    l1 = Ledger(path=str(p1))
    l2 = Ledger(path=str(p2))
    # Same entries, same order
    for ledger in (l1, l2):
        ledger.append({"t": 100, "ns": "alice", "op": "BIND", "cell": "abc", "g": 0.0})
        ledger.append({"t": 200, "ns": "alice", "op": "TICK", "g": 0.0})
        ledger.close()
    assert p1.read_bytes() == p2.read_bytes()


def test_ledger_rejects_entry_with_hash(tmp_ledger):
    with pytest.raises(stitch.LedgerError):
        tmp_ledger.append({"t": 1, "ns": "alice", "op": "BIND", "cell": "a", "g": 0.0, "h": "abc"})


# ============================================================================
# Stitches — data plane
# ============================================================================

def test_bind_creates_cell(alice, tmp_ledger):
    cell = bind(alice, tmp_ledger, kind="task", payload={"goal": "summarize"})
    assert cell.id  # 16-char hex
    assert len(cell.id) == 16
    assert cell.tenant == "alice"
    assert cell.kind == "task"
    assert cell.payload == {"goal": "summarize"}
    assert cell.created_at > 0


def test_bind_charges_cost(alice, tmp_ledger):
    bind(alice, tmp_ledger, kind="task", payload={}, cost=0.5)
    assert alice.gamma == 0.5


def test_bind_rejects_non_dict_payload(alice, tmp_ledger):
    with pytest.raises(stitch.exceptions.BindError):
        bind(alice, tmp_ledger, kind="task", payload="not a dict")


def test_link_no_gamma_cost(alice, tmp_ledger):
    a = bind(alice, tmp_ledger, kind="task", payload={})
    b = bind(alice, tmp_ledger, kind="task", payload={})
    gamma_before = alice.gamma
    link(alice, tmp_ledger, a, b, "depends_on")
    assert alice.gamma == gamma_before  # no cost


def test_effect_charges_surprise(alice, tmp_ledger, surprise):
    cell = bind(alice, tmp_ledger, kind="task", payload={})
    ok = effect(alice, tmp_ledger, surprise, cell, "status",
                predicted={"status": "done"},
                observed={"status": "error"})
    assert ok
    assert alice.gamma == 1.0  # 1 bit of surprise
    assert cell.payload["status"] == "error"


def test_effect_rejects_when_budget_exhausted(alice, tmp_ledger, surprise):
    cell = bind(alice, tmp_ledger, kind="task", payload={})
    # Charge up to near the budget
    alice.charge(LOG2_3 - 0.5)
    # Now a 1-bit surprise should be rejected
    ok = effect(alice, tmp_ledger, surprise, cell, "status",
                predicted={"status": "done"},
                observed={"status": "error"})
    assert not ok
    # γ should be unchanged, cell payload should not be mutated
    assert alice.gamma == pytest.approx(LOG2_3 - 0.5)
    assert "status" not in cell.payload


def test_effect_strict_mode_raises(alice, tmp_ledger, surprise):
    cell = bind(alice, tmp_ledger, kind="task", payload={})
    alice.charge(LOG2_3 - 0.5)
    with pytest.raises(EffectRejected):
        effect(alice, tmp_ledger, surprise, cell, "status",
               predicted={"status": "done"},
               observed={"status": "error"},
               strict=True)


def test_view_returns_payload(alice, tmp_ledger):
    cell = bind(alice, tmp_ledger, kind="task", payload={"goal": "summarize"})
    assert view(cell, "goal") == "summarize"
    assert view(cell) == {"goal": "summarize"}


def test_tick_resets_gamma(alice, tmp_ledger):
    cell = bind(alice, tmp_ledger, kind="task", payload={})
    surprise = KLSurprise()
    effect(alice, tmp_ledger, surprise, cell, "k",
           predicted={"k": "a"}, observed={"k": "b"})
    assert alice.gamma == 1.0
    tick(alice, tmp_ledger)
    assert alice.gamma == 0.0
    assert alice.eta == LOG2_3


# ============================================================================
# Stitches — governance plane
# ============================================================================

def test_attest_returns_signature(alice, tmp_ledger):
    cell = bind(alice, tmp_ledger, kind="task", payload={})
    sig = attest(alice, tmp_ledger, cell, claim="completed")
    assert len(sig) == 16  # 16 hex chars in v0.1


def test_delegate_logs(alice, tmp_ledger):
    cell = bind(alice, tmp_ledger, kind="task", payload={})
    delegate(alice, tmp_ledger, cell, to_identity="bob", scope="read")
    entries = list(tmp_ledger.read_all())
    assert any(e["op"] == "DELEGATE" and e["to"] == "bob" for e in entries)


def test_contest_logs(alice, tmp_ledger):
    cell = bind(alice, tmp_ledger, kind="task", payload={})
    contest(alice, tmp_ledger, cell, reason="suspicious")
    entries = list(tmp_ledger.read_all())
    assert any(e["op"] == "CONTEST" and e["reason"] == "suspicious" for e in entries)


def test_merger_creates_new_cell(alice, tmp_ledger):
    a = bind(alice, tmp_ledger, kind="task", payload={"x": 1})
    b = bind(alice, tmp_ledger, kind="task", payload={"y": 2})
    merged = merger(alice, tmp_ledger, a, b, kind="merged", payload={"x": 1, "y": 2})
    assert merged.parent_hash == a.id
    assert merged.payload == {"x": 1, "y": 2}
    entries = list(tmp_ledger.read_all())
    assert any(e["op"] == "MERGER" and e["a"] == a.id and e["b"] == b.id for e in entries)


def test_revoke_logs(alice, tmp_ledger):
    cell = bind(alice, tmp_ledger, kind="task", payload={})
    revoke(alice, tmp_ledger, cell, reason="breach")
    entries = list(tmp_ledger.read_all())
    assert any(e["op"] == "REVOKE" and e["reason"] == "breach" for e in entries)


def test_withdraw_logs_amount(alice, tmp_ledger):
    withdraw(alice, tmp_ledger, amount=0.5)
    entries = list(tmp_ledger.read_all())
    assert any(e["op"] == "WITHDRAW" and e["amount"] == 0.5 for e in entries)


# ============================================================================
# Integration: a full agent loop
# ============================================================================

def test_full_agent_loop(alice, tmp_ledger, surprise):
    """End-to-end: bind a task, observe, attest, tick, verify ledger."""
    # Turn 1: predict done, observe error (1 bit of surprise)
    task1 = bind(alice, tmp_ledger, kind="task", payload={"goal": "task1"})
    ok = effect(alice, tmp_ledger, surprise, task1, "status",
                predicted={"status": "done"},
                observed={"status": "error"})
    assert ok
    assert alice.gamma == 1.0
    attest(alice, tmp_ledger, task1, claim="completed_turn_1")
    tick(alice, tmp_ledger)
    assert alice.gamma == 0.0

    # Turn 2: predict done, observe done (0 surprise)
    task2 = bind(alice, tmp_ledger, kind="task", payload={"goal": "task2"})
    ok = effect(alice, tmp_ledger, surprise, task2, "status",
                predicted={"status": "done"},
                observed={"status": "done"})
    assert ok
    assert alice.gamma == 0.0  # no surprise

    # Ledger should verify
    assert tmp_ledger.verify()

    # Should have BIND, EFFECT, ATTEST, TICK, BIND, EFFECT entries
    entries = list(tmp_ledger.read_all())
    ops = [e["op"] for e in entries]
    assert ops == ["BIND", "EFFECT", "ATTEST", "TICK", "BIND", "EFFECT"]


# ============================================================================
# Byte-exactness: identical input sequences produce identical ledgers
# ============================================================================

def test_byte_exactness_across_runs(tmp_path):
    """Same inputs in two ledgers produce byte-identical files (modulo timestamps)."""
    # We need to mock _now_ns to get deterministic timestamps
    import stitch.stitches as stitches_mod
    original_now = stitches_mod._now_ns

    counter = [0]
    def fake_now():
        counter[0] += 1
        return counter[0] * 1_000_000_000

    try:
        stitches_mod._now_ns = fake_now
        # Run 1
        p1 = tmp_path / "a.jsonl"
        l1 = Ledger(path=str(p1))
        a1 = Tenant(identity="alice")
        bind(a1, l1, kind="task", payload={"goal": "x"})
        s = KLSurprise()
        cell = bind(a1, l1, kind="task", payload={})
        effect(a1, l1, s, cell, "k", {"k": "a"}, {"k": "b"})
        tick(a1, l1)
        l1.close()

        # Run 2 — reset counter
        counter[0] = 0
        p2 = tmp_path / "b.jsonl"
        l2 = Ledger(path=str(p2))
        a2 = Tenant(identity="alice")
        bind(a2, l2, kind="task", payload={"goal": "x"})
        cell2 = bind(a2, l2, kind="task", payload={})
        effect(a2, l2, s, cell2, "k", {"k": "a"}, {"k": "b"})
        tick(a2, l2)
        l2.close()

        assert p1.read_bytes() == p2.read_bytes()
    finally:
        stitches_mod._now_ns = original_now
