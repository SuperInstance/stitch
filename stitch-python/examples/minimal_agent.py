"""
Minimal agent demo — using stitch in a normal Python agent loop.

This demo uses a FAKE "LLM" (a deterministic function) so it runs without
API keys or network. The point is to show how `stitch` slots into a normal
agent loop and makes state accountable.

Run: python stitch-python/examples/minimal_agent.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# Add the parent directory to sys.path so we can import stitch without installing
sys.path.insert(0, str(Path(__file__).parent.parent))

import stitch
from stitch import (
    Tenant, Ledger, KLSurprise,
    bind, effect, view, tick, attest, delegate, contest, merger, revoke, withdraw,
)


# ============================================================================
# A fake "LLM" — deterministic, no network. Real code would call openai/anthropic/etc.
# ============================================================================

def fake_llm(prompt: str) -> str:
    """Pretend to be an LLM. Returns a deterministic response."""
    if "summarize" in prompt.lower():
        return "Summary: this is a summary."
    if "translate" in prompt.lower():
        return "Translation: hola mundo"
    return f"Response to: {prompt[:30]}..."


# ============================================================================
# The agent loop — uses stitch for accountable state
# ============================================================================

def main():
    print("=" * 70)
    print("stitch — minimal agent demo")
    print("=" * 70)

    # Set up the substrate
    alice = Tenant(identity="alice")  # default budget = log₂3 ≈ 1.585 bits
    surprise = KLSurprise()

    # Use a temp file for the ledger so the demo is repeatable
    ledger_path = os.path.join(tempfile.gettempdir(), "stitch_demo_state.jsonl")
    if os.path.exists(ledger_path):
        os.remove(ledger_path)  # start fresh
    ledger = Ledger(path=ledger_path)

    print(f"\nTenant: {alice}")
    print(f"Budget: γ + η ≤ {alice.budget:.4f} bits per tick (log₂3)")
    print(f"Ledger: {ledger_path}\n")

    # --- Turn 1: predict "summary" but observe something different ---
    print("-" * 70)
    print("Turn 1: User asks to summarize a document")
    print("-" * 70)

    turn1 = bind(alice, ledger, kind="turn", payload={"user_input": "summarize the doc"})

    # Predict what the LLM will produce
    predicted = {"summary": "TODO — summary not yet generated"}

    # Call the (fake) LLM
    response = fake_llm("summarize the doc")
    observed = {"summary": response}

    print(f"  Predicted: {predicted}")
    print(f"  Observed:  {observed}")

    # EFFECT — this computes surprise and charges γ
    ok = effect(alice, ledger, surprise, turn1, "summary", predicted, observed)
    if ok:
        s = surprise.compute(predicted, observed)
        print(f"  ✓ Effect committed (surprise = {s:.1f} bits, γ now = {alice.gamma:.4f})")
    else:
        print(f"  ✗ Effect rejected — conservation law violated (γ = {alice.gamma:.4f})")

    # Attest to the turn
    sig = attest(alice, ledger, turn1, claim="completed_turn_1")
    print(f"  Attested: sig={sig}")

    # Tick to reset γ for the next turn
    tick(alice, ledger)
    print(f"  Ticked: γ reset to {alice.gamma:.4f}")

    # --- Turn 2: try to violate the conservation law ---
    print()
    print("-" * 70)
    print("Turn 2: Try to force many surprises (will exhaust the budget)")
    print("-" * 70)

    turn2 = bind(alice, ledger, kind="turn", payload={"user_input": "test budget"})

    # Three writes that each cost 1 bit of surprise — the third should fail
    for i in range(3):
        predicted = {"k": "predicted"}
        observed = {"k": f"unexpected_value_{i}"}
        ok = effect(alice, ledger, surprise, turn2, "k", predicted, observed)
        if ok:
            print(f"  Write {i+1}: ✓ committed (γ now = {alice.gamma:.4f}, η = {alice.eta:.4f})")
        else:
            print(f"  Write {i+1}: ✗ REJECTED (γ = {alice.gamma:.4f}, η = {alice.eta:.4f})")
            print(f"           Conservation law would be violated.")
            print(f"           The agent must either tick (reset γ) or accept the budget is exhausted.")

    # Tick and retry
    print()
    print("  → Tick to reset γ, then retry the failed write...")
    tick(alice, ledger)
    ok = effect(alice, ledger, surprise, turn2, "k",
                predicted={"k": "predicted"},
                observed={"k": "unexpected_value_retry"})
    print(f"  Retry: {'✓ committed' if ok else '✗ rejected'} (γ now = {alice.gamma:.4f})")

    # --- Demonstrate governance stitches ---
    print()
    print("-" * 70)
    print("Governance: delegate, contest, revoke")
    print("-" * 70)

    # Delegate read access to bob
    delegate(alice, ledger, turn1, to_identity="bob", scope="read")
    print(f"  Delegated read access on {turn1.id} to bob")

    # Bob (a new tenant) contests the result
    bob = Tenant(identity="bob")
    contest(bob, ledger, turn1, reason="suspicious summary — looks templated")
    print(f"  Bob contested {turn1.id}")

    # Revoke the contested cell
    revoke(alice, ledger, turn1, reason="contested by bob, removing authority")
    print(f"  Alice revoked {turn1.id}")

    # Withdraw remaining budget
    withdraw(alice, ledger)
    print(f"  Alice withdrew remaining η = {alice.budget - alice.gamma:.4f}")

    # --- Verify the ledger ---
    print()
    print("-" * 70)
    print("Ledger verification")
    print("-" * 70)

    is_valid = ledger.verify()
    print(f"  Hash chain valid: {is_valid}")

    # Show the ledger contents
    print()
    print("  Ledger contents:")
    print("  " + "-" * 66)
    for i, entry in enumerate(ledger.read_all()):
        op = entry["op"]
        ns = entry["ns"]
        g = entry.get("g", 0.0)
        s = entry.get("s", "")
        h = entry["h"]
        s_str = f"s={s:.1f}" if s != "" else "       "
        print(f"  {i+1:2}. {op:10} {ns:8} γ={g:.4f} {s_str}  h={h}")
    print("  " + "-" * 66)

    # --- Final state ---
    print()
    print("-" * 70)
    print("Final state")
    print("-" * 70)
    print(f"  {alice}")
    print(f"  Ledger file: {ledger_path}")
    print(f"  Total entries: {sum(1 for _ in ledger.read_all())}")

    print()
    print("=" * 70)
    print("Demo complete. The agent's state is now accountable:")
    print("  • Every write computed surprise and charged γ")
    print("  • The conservation law γ+η≤log₂3 was enforced (one write was rejected)")
    print("  • The ledger is tamper-evident (FNV-1a 64-bit hash chain)")
    print("  • Every operation is replayable from the JSONL file")
    print("  • The agent's tenants (alice, bob) have separate budgets")
    print("=" * 70)

    ledger.close()


if __name__ == "__main__":
    main()
