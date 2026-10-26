"""
Cross-language byte-exactness test harness.

PURPOSE:
    Verifies that the Python, Rust, TypeScript, and C99 implementations of
    `stitch` produce byte-identical ledger output for identical inputs.

STATUS:
    v0.1: The Python reference is checked here for determinism, and the
    ledger layer is checked byte-for-byte against the Rust implementation
    via fixed reference vectors: this file asserts the Python hashes for
    two fixed entries, and `stitch-rs`'s test
    `test_cross_lang_byte_exact_with_python` asserts the identical hashes
    and file bytes on the Rust side. Run both suites to verify the pair.
    Full-sequence cross-language comparison (all 11 stitches) awaits the
    Rust implementation growing beyond its current skeleton (bind only).

USAGE:
    python tests/cross_lang.py          # Python side (this file)
    (cd stitch-rs && cargo test)       # Rust side (matching vectors)
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Make the Python stitch importable
sys.path.insert(0, str(Path(__file__).parent.parent / "stitch-python"))

import stitch
from stitch import Tenant, Ledger, KLSurprise, bind, effect, tick, attest
from stitch.stitches import _now_ns


# ============================================================================
# Test sequence — the canonical input that all implementations must agree on
# ============================================================================

def run_canonical_sequence(ledger_path: str, timestamp_seed: int = 1_000_000_000) -> bytes:
    """
    Run a fixed sequence of stitches with deterministic timestamps.
    Returns the ledger file bytes.

    This is the canonical test input. Any conformant implementation must
    produce byte-identical output to this for the same inputs.
    """
    # Override _now_ns to be deterministic
    original_now = _now_ns
    counter = [0]
    def fake_now():
        counter[0] += 1
        return counter[0] * timestamp_seed

    try:
        import stitch.stitches as stitches_mod
        stitches_mod._now_ns = fake_now

        ledger = Ledger(path=ledger_path)
        t = Tenant(identity="alice")

        # BIND
        cell = bind(t, ledger, kind="task", payload={"goal": "summarize"})

        # EFFECT (1 bit of surprise)
        s = KLSurprise()
        ok = effect(t, ledger, s, cell, "status",
                    predicted={"status": "done"},
                    observed={"status": "error"})
        assert ok

        # ATTEST
        attest(t, ledger, cell, claim="completed")

        # TICK
        tick(t, ledger)

        ledger.close()
    finally:
        import stitch.stitches as stitches_mod
        stitches_mod._now_ns = original_now

    with open(ledger_path, "rb") as f:
        return f.read()


# ============================================================================
# Python implementation test (always passes — it's the reference)
# ============================================================================

def test_python_canonical_sequence(tmp_path):
    """Run the canonical sequence through Python, twice. Verify byte-identical."""
    p1 = tmp_path / "python_run1.jsonl"
    p2 = tmp_path / "python_run2.jsonl"
    bytes1 = run_canonical_sequence(str(p1))
    bytes2 = run_canonical_sequence(str(p2))
    assert bytes1 == bytes2, "Python implementation is not deterministic!"
    print(f"  ✓ Python: {len(bytes1)} bytes, deterministic")


# ============================================================================
# Ledger-layer reference vectors (checked against Rust — see stitch-rs tests)
# ============================================================================

# Fixed entries and the hashes the Python reference produces for them.
# stitch-rs/src/lib.rs test_cross_lang_byte_exact_with_python asserts the
# identical hashes and file bytes from the Rust implementation.
REFERENCE_ENTRIES = [
    {"t": 1000, "ns": "alice", "op": "BIND", "cell": "abc", "g": 0.0},
    {"t": 2000, "ns": "alice", "op": "TICK", "g": 0.0},
]
REFERENCE_HASHES = ["b513cabe780e2894", "24c56ec21ef20816"]


def test_python_ledger_reference_vectors(tmp_path):
    """Python must produce the reference hashes the Rust test pins."""
    path = tmp_path / "reference.jsonl"
    ledger = Ledger(path=str(path))
    hashes = [ledger.append(dict(e)) for e in REFERENCE_ENTRIES]
    ledger.close()
    assert hashes == REFERENCE_HASHES, (
        f"Python ledger hashes {hashes} != reference {REFERENCE_HASHES}"
    )
    print(f"  ✓ Python ledger vectors: {hashes} (Rust asserts the same)")


# ============================================================================
# Main entry point — run all available cross-lang tests
# ============================================================================

def main():
    print("=" * 70)
    print("stitch — cross-language byte-exactness test harness")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        print("\n[1/2] Python reference determinism (must always pass):")
        test_python_canonical_sequence(tmp_path)

        print("\n[2/2] Ledger reference vectors (Rust asserts identical bytes):")
        test_python_ledger_reference_vectors(tmp_path)

    print()
    print("=" * 70)
    print("Cross-language byte-exactness: Python side PASSED.")
    print("Run (cd stitch-rs && cargo test) for the matching Rust side.")
    print("=" * 70)


if __name__ == "__main__":
    main()
