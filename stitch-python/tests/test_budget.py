"""
Tests for v0.3 tenant budget declarations.

Closes the v0.2 known limitation: "Budgets are not recorded in the ledger.
An auditor must assume each tenant's configured budget (default log₂3).
A tenant running a custom budget cannot prove it from its ledger alone."

v0.3 fix: `declare_budget()` writes a BIND entry with kind="tenant"
and payload={"budget": <float>}. The audit reads these declarations
and uses per-tenant budgets instead of assuming one global budget.
"""

import pytest
from pathlib import Path

import stitch
from stitch import (
    Tenant, Ledger, KLSurprise,
    bind, effect, tick, declare_budget,
    audit_ledger, agree, agreed_state,
    LOG2_3,
)


# ============================================================================
# declare_budget helper
# ============================================================================

def test_declare_budget_writes_bind_entry(tmp_path):
    """declare_budget writes a BIND entry with kind='tenant' and the budget in payload."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice", budget=3.0)
    declare_budget(alice, ledger)
    ledger.close()

    entries = list(ledger.read_all())
    assert len(entries) == 1
    e = entries[0]
    assert e["op"] == "BIND"
    assert e["kind"] == "tenant"
    assert e["payload"] == {"budget": 3.0}
    assert e["ns"] == "alice"


def test_declare_budget_default_uses_tenant_budget(tmp_path):
    """If budget=None, declare_budget uses the tenant's currently configured budget."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice", budget=2.5)
    declare_budget(alice, ledger, budget=None)
    ledger.close()

    e = list(ledger.read_all())[0]
    assert e["payload"]["budget"] == 2.5


def test_declare_budget_explicit_overrides_tenant(tmp_path):
    """If budget is given, it overrides the tenant's configured budget."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice", budget=2.5)
    declare_budget(alice, ledger, budget=4.0)
    ledger.close()

    e = list(ledger.read_all())[0]
    assert e["payload"]["budget"] == 4.0


# ============================================================================
# Audit reads declared budgets
# ============================================================================

def test_audit_uses_declared_budget(tmp_path):
    """Audit uses the declared budget instead of the default."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice", budget=3.0)
    declare_budget(alice, ledger)  # records budget=3.0
    cell = bind(alice, ledger, kind="task", payload={})
    s = KLSurprise()
    # Two effects of 1 bit each = γ=2.0, which fits in budget=3.0
    effect(alice, ledger, s, cell, "k1", {"k1": "a"}, {"k1": "b"})
    effect(alice, ledger, s, cell, "k2", {"k2": "a"}, {"k2": "b"})
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.chain_ok
    assert verdict.law_ok, f"Should pass with declared budget=3.0: {verdict.law_violations}"
    assert verdict.budgets == {"alice": 3.0}
    # Fallback budget field stays at default for backward compat
    assert verdict.budget == LOG2_3


def test_audit_falls_back_to_default_without_declaration(tmp_path):
    """Without a declaration, audit uses the fallback budget (default LOG2_3)."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice")  # default budget = LOG2_3
    cell = bind(alice, ledger, kind="task", payload={})
    s = KLSurprise()
    effect(alice, ledger, s, cell, "k", {"k": "a"}, {"k": "b"})  # 1 bit
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.chain_ok
    assert verdict.law_ok
    assert verdict.budgets == {}  # no declarations
    assert verdict.budget == LOG2_3  # fallback


def test_audit_detects_violation_with_declared_budget(tmp_path):
    """A tenant with a declared budget that's exceeded is flagged as a violation."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    # Tenant declares a TINY budget
    alice = Tenant(identity="alice")
    declare_budget(alice, ledger, budget=0.5)
    cell = bind(alice, ledger, kind="task", payload={})
    s = KLSurprise()
    # 1 bit of surprise > 0.5 declared budget → violation
    effect(alice, ledger, s, cell, "k", {"k": "a"}, {"k": "b"})
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.chain_ok
    assert not verdict.law_ok, "Should flag violation: γ=1.0 > declared 0.5"
    assert verdict.budgets == {"alice": 0.5}
    assert any("exceeds budget 0.5" in v for v in verdict.law_violations)


def test_audit_per_tenant_budgets_in_one_ledger(tmp_path):
    """Multiple tenants in one ledger, each with their own declared budget."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    declare_budget(alice, ledger, budget=0.5)
    declare_budget(bob, ledger, budget=3.0)

    cell_a = bind(alice, ledger, kind="task", payload={})
    cell_b = bind(bob, ledger, kind="task", payload={})
    s = KLSurprise()
    # Alice: 1 bit > 0.5 budget → violation
    effect(alice, ledger, s, cell_a, "k", {"k": "a"}, {"k": "b"})
    # Bob: 1 bit, fits in 3.0 budget → OK
    effect(bob, ledger, s, cell_b, "k", {"k": "a"}, {"k": "b"})
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.budgets == {"alice": 0.5, "bob": 3.0}
    # Alice's violation should be in the list
    alice_violation = [v for v in verdict.law_violations if "alice" in v and "exceeds" in v]
    assert len(alice_violation) == 1
    # Bob should have no violations
    bob_violations = [v for v in verdict.law_violations if "bob" in v and "exceeds" in v]
    assert len(bob_violations) == 0


def test_audit_first_declaration_wins(tmp_path):
    """If a tenant declares multiple budgets, the first one is authoritative."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice")
    declare_budget(alice, ledger, budget=3.0)  # first — wins
    declare_budget(alice, ledger, budget=0.1)  # second — ignored by audit
    cell = bind(alice, ledger, kind="task", payload={})
    s = KLSurprise()
    # 1 bit fits in 3.0 (first declaration)
    effect(alice, ledger, s, cell, "k", {"k": "a"}, {"k": "b"})
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.budgets == {"alice": 3.0}  # first declaration wins
    assert verdict.law_ok, f"Should pass with first-declared budget 3.0: {verdict.law_violations}"


# ============================================================================
# Backward compat: v0.2 ledgers (no declarations) still audit correctly
# ============================================================================

def test_v02_ledger_without_declaration_still_audits(tmp_path):
    """A ledger with no budget declarations (v0.2 style) audits with the fallback budget."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice")  # default LOG2_3
    cell = bind(alice, ledger, kind="task", payload={})
    s = KLSurprise()
    # γ=1.0 fits in default LOG2_3
    effect(alice, ledger, s, cell, "k", {"k": "a"}, {"k": "b"})
    tick(alice, ledger)
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.ok, f"v0.2-style ledger should audit clean: {verdict.law_violations}"
    assert verdict.budgets == {}  # no declarations
    assert verdict.budget == LOG2_3


# ============================================================================
# Agreement with declared budgets
# ============================================================================

def test_agreement_works_with_declared_budgets(tmp_path):
    """agree() works when both ledgers have declared budgets."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    declare_budget(alice, la, budget=3.0)
    declare_budget(bob, lb, budget=2.5)

    # Book an exchange — should mint
    payload = {"doc": "exchange-1"}
    stitch.book_send(alice, la, xchg="x1", peer="bob", payload=payload)
    stitch.book_receive(bob, lb, xchg="x1", peer="alice", payload=payload)
    la.close()
    lb.close()

    verdict = agree(str(pa), str(pb), "x1")
    assert verdict.agreed, f"Should mint with declared budgets: {verdict.detail}"


def test_agreement_refuses_if_one_side_law_violates_with_declared_budget(tmp_path):
    """If one side has a declared budget that's exceeded, agree refuses with LAW_VIOLATED."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    # Alice declares a tiny budget but writes a 1-bit surprise
    declare_budget(alice, la, budget=0.5)
    cell = bind(alice, la, kind="task", payload={})
    s = KLSurprise()
    effect(alice, la, s, cell, "k", {"k": "a"}, {"k": "b"})  # γ=1.0 > 0.5 declared

    declare_budget(bob, lb, budget=3.0)
    payload = {"doc": "x1"}
    # Book the exchange in alice's ledger (after the violation)
    stitch.book_send(alice, la, xchg="x1", peer="bob", payload=payload)
    stitch.book_receive(bob, lb, xchg="x1", peer="alice", payload=payload)
    la.close()
    lb.close()

    verdict = agree(str(pa), str(pb), "x1")
    assert not verdict.agreed
    assert verdict.reason == stitch.LAW_VIOLATED


# ============================================================================
# Position-aware declarations (patch applied on adoption of v0.3)
#
# The v0.3 draft extracted declared budgets in a pre-pass over the whole
# ledger, so a declaration appended AFTER an overspend retroactively
# excused it. Declarations now take effect at their own position: they
# cover the declaration entry and later entries, never earlier ones.
# ============================================================================

def test_audit_declaration_after_overspend_does_not_excuse_it(tmp_path):
    """Regression test for the pre-pass hole: spend γ=2.0 under the
    fallback budget (log₂3 ≈ 1.585), THEN declare 3.0. The audit must
    still report the violation — the declaration cannot reach back."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    # Write side is permissive (configured budget 10) — the ledger is
    # what a tenant could produce; the audit is the judge.
    alice = Tenant(identity="alice", budget=10.0)
    cell = bind(alice, ledger, kind="task", payload={})
    s = KLSurprise()
    effect(alice, ledger, s, cell, "k1", {"k1": "a"}, {"k1": "b"})  # γ=1.0
    effect(alice, ledger, s, cell, "k2", {"k2": "a"}, {"k2": "b"})  # γ=2.0
    # Only now does the 3.0 declaration appear in the ledger.
    declare_budget(alice, ledger, budget=3.0)
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.chain_ok
    # The claim is recorded...
    assert verdict.budgets == {"alice": 3.0}
    # ...but it does not cover the entries written before it stood.
    assert not verdict.law_ok, (
        f"Overspend before the declaration must still violate: "
        f"{verdict.law_violations}")
    assert any("exceeds budget 1.585" in v for v in verdict.law_violations)


def test_audit_declaration_covers_entries_from_its_position_on(tmp_path):
    """A declaration mid-ledger applies forward: spending that was legal
    under the fallback before it, plus more after it up to the declared
    budget, audits clean."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    alice = Tenant(identity="alice", budget=10.0)
    cell = bind(alice, ledger, kind="task", payload={})
    s = KLSurprise()
    effect(alice, ledger, s, cell, "k1", {"k1": "a"}, {"k1": "b"})  # γ=1.0 ≤ log₂3
    declare_budget(alice, ledger, budget=3.0)
    effect(alice, ledger, s, cell, "k2", {"k2": "a"}, {"k2": "b"})  # γ=2.0 ≤ 3.0
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.chain_ok
    assert verdict.law_ok, f"Should pass: {verdict.law_violations}"
    assert verdict.budgets == {"alice": 3.0}
