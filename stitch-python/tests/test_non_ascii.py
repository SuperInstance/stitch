"""
Non-ASCII byte-exactness tests — verifies the v0.3 encoding pin.

Pin: all `json.dumps` calls in the library use `ensure_ascii=False`, so
non-ASCII content is emitted as raw UTF-8 bytes — matching serde_json on
the Rust side. This closes the v0.2 VERIFICATION.md known limitation:
"Non-ASCII divergence. Python's json.dumps escapes non-ASCII by default;
Rust's serde_json emits raw UTF-8. Entries containing non-ASCII text
therefore hash differently across implementations."

These tests verify:
  - Ledger bytes are identical for two runs of the same non-ASCII content
  - The hash chain verifies when entries contain non-ASCII content
  - Cell IDs are deterministic for non-ASCII payloads
  - The agreement layer's payload_hash is consistent for non-ASCII payloads
  - Foreign-ledger audit (audit_ledger) verifies non-ASCII ledgers
  - Agreement mints correctly when the exchanged payload contains non-ASCII
"""

import json
import os
import pytest
from pathlib import Path

import stitch
from stitch import (
    Tenant, Ledger, KLSurprise,
    bind, effect, view, tick, attest,
    payload_hash, book_send, book_receive,
    audit_ledger, agree, agreed_state,
)
from stitch.stitches import _now_ns


# ============================================================================
# Test fixtures: non-ASCII content categories
# ============================================================================

NON_ASCII_PAYLOADS = [
    # Latin-1 accented chars (Western European)
    {"task": "résumé", "status": "café"},
    # Chinese
    {"task": "总结文档", "status": "完成"},
    # Japanese
    {"task": "レポートを要約", "status": "完了"},
    # Korean
    {"task": "문서 요약", "status": "완료"},
    # Emoji
    {"task": "summarize 📄", "status": "done ✅"},
    # Mixed scripts + emoji
    {"task": "summary of café 📄 — 已完成", "status": "done ✓"},
    # Cyrillic
    {"task": "обобщить документ", "status": "готово"},
    # Arabic (RTL)
    {"task": "تلخيص المستند", "status": "تم"},
    # Math symbols
    {"task": "compute ∫₀¹ x² dx = ⅓", "status": "≈0.333"},
]


# ============================================================================
# Byte-exactness: same non-ASCII content → identical ledger bytes
# ============================================================================

@pytest.mark.parametrize("payload", NON_ASCII_PAYLOADS)
def test_non_ascii_deterministic_bytes(tmp_path, payload):
    """Two ledgers with identical non-ASCII input produce byte-identical files."""
    import stitch.stitches as stitches_mod
    original_now = stitches_mod._now_ns
    counter = [0]
    def fake_now():
        counter[0] += 1
        return counter[0] * 1_000_000_000

    try:
        stitches_mod._now_ns = fake_now
        bytes_runs = []
        for run in range(2):
            counter[0] = 0
            p = tmp_path / f"run_{run}.jsonl"
            ledger = Ledger(path=str(p))
            t = Tenant(identity="alice")
            bind(t, ledger, kind="task", payload=payload)
            s = KLSurprise()
            effect(t, ledger, s, type("c", (), {"id": "abc", "payload": {}})(),
                   "k", {"k": "a"}, {"k": payload["status"]})
            tick(t, ledger)
            ledger.close()
            bytes_runs.append(p.read_bytes())
        assert bytes_runs[0] == bytes_runs[1], (
            f"Non-deterministic bytes for payload {payload!r}\n"
            f"Run 1: {bytes_runs[0]!r}\nRun 2: {bytes_runs[1]!r}"
        )
    finally:
        stitches_mod._now_ns = original_now


# ============================================================================
# Hash chain verifies with non-ASCII content
# ============================================================================

@pytest.mark.parametrize("payload", NON_ASCII_PAYLOADS)
def test_non_ascii_hash_chain_verifies(tmp_path, payload):
    """Ledger.verify() returns True for non-ASCII content."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    t = Tenant(identity="alice")
    bind(t, ledger, kind="task", payload=payload)
    ledger.close()
    # Re-open and verify
    ledger2 = Ledger(path=str(p))
    assert ledger2.verify()


# ============================================================================
# Cell IDs are deterministic for non-ASCII payloads
# ============================================================================

@pytest.mark.parametrize("payload", NON_ASCII_PAYLOADS)
def test_non_ascii_cell_id_deterministic(tmp_path, payload):
    """Same non-ASCII payload + same timestamp → same cell ID."""
    import stitch.stitches as stitches_mod
    original_now = stitches_mod._now_ns
    fake_now = lambda: 1_000_000_000  # constant
    stitches_mod._now_ns = fake_now
    try:
        from stitch.stitches import _cell_id
        cid1 = _cell_id("alice", "task", payload, None, 1_000_000_000)
        cid2 = _cell_id("alice", "task", payload, None, 1_000_000_000)
        assert cid1 == cid2
        assert len(cid1) == 16
    finally:
        stitches_mod._now_ns = original_now


# ============================================================================
# payload_hash is consistent for non-ASCII payloads
# ============================================================================

@pytest.mark.parametrize("payload", NON_ASCII_PAYLOADS)
def test_non_ascii_payload_hash_consistent(payload):
    """payload_hash returns the same value for the same non-ASCII payload."""
    h1 = payload_hash(payload)
    h2 = payload_hash(payload)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_payload_hash_uses_raw_utf8_bytes():
    """Verify the hash is computed over raw UTF-8 bytes, not \\uXXXX escapes.

    This is the v0.3 pin: if we changed ensure_ascii back to True, the hash
    would differ. This test pins the policy.
    """
    import hashlib
    payload = {"task": "café"}
    # Expected: raw UTF-8 form
    canonical_raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False)
    expected_hash = hashlib.sha256(canonical_raw.encode("utf-8")).hexdigest()
    assert payload_hash(payload) == expected_hash
    # Sanity check: the escaped form would give a DIFFERENT hash
    canonical_escaped = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True)
    escaped_hash = hashlib.sha256(canonical_escaped.encode("utf-8")).hexdigest()
    assert escaped_hash != expected_hash, "ensure_ascii=True and False should produce different hashes for non-ASCII"


# ============================================================================
# Foreign-ledger audit verifies non-ASCII ledgers
# ============================================================================

def test_audit_ledger_non_ascii(tmp_path):
    """audit_ledger returns chain_ok=True for a clean non-ASCII ledger."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    t = Tenant(identity="alice")
    bind(t, ledger, kind="task", payload={"task": "总结文档", "status": "完成"})
    s = KLSurprise()
    cell = bind(t, ledger, kind="turn", payload={})
    effect(t, ledger, s, cell, "k", {"k": "a"}, {"k": "完成"})
    tick(t, ledger)
    ledger.close()

    verdict = audit_ledger(str(p))
    assert verdict.chain_ok, f"Chain should verify: {verdict}"
    assert verdict.law_ok, f"Law should verify: {verdict.law_violations}"
    assert verdict.entries == 4


# ============================================================================
# Agreement mints correctly with non-ASCII payloads
# ============================================================================

def test_agreement_mints_with_non_ascii_payload(tmp_path):
    """Two ledgers that book the same non-ASCII payload agree; mismatched ones refuse."""
    payload_send = {"doc": "harbor-chart-中文", "rev": 3, "note": "café ☕"}
    payload_recv_same = {"doc": "harbor-chart-中文", "rev": 3, "note": "café ☕"}
    payload_recv_diff = {"doc": "harbor-chart-中文", "rev": 3, "note": "tea ☕"}  # different note

    # Same payload → mint
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")
    book_send(alice, la, xchg="x1", peer="bob", payload=payload_send)
    book_receive(bob, lb, xchg="x1", peer="alice", payload=payload_recv_same)
    la.close()
    lb.close()

    verdict = agree(str(pa), str(pb), "x1")
    assert verdict.agreed, f"Should mint: {verdict.detail}"
    # The payload hash both sides committed to
    assert verdict.payload_hash == payload_hash(payload_send)

    # Different payload on the SAME exchange id → PAYLOAD_MISMATCH.
    # The peer check must pass first (alice SENDs to bob; bob RECEIVEs from
    # alice), so the payload check is what fires.
    pb2 = tmp_path / "bob_mismatch.jsonl"
    lb2 = Ledger(path=str(pb2))
    bob2 = Tenant(identity="bob")  # same identity — peer check passes
    book_receive(bob2, lb2, xchg="x1", peer="alice", payload=payload_recv_diff)
    lb2.close()

    verdict_mismatch = agree(str(pa), str(pb2), "x1")
    assert not verdict_mismatch.agreed, "Mismatched payload should not mint"
    assert verdict_mismatch.reason == stitch.PAYLOAD_MISMATCH, (
        f"Expected PAYLOAD_MISMATCH, got {verdict_mismatch.reason}: {verdict_mismatch.detail}"
    )


# ============================================================================
# agreed_state folds non-ASCII exchanges correctly
# ============================================================================

def test_agreed_state_with_non_ascii(tmp_path):
    """agreed_state folds multiple exchanges including non-ASCII payloads."""
    pa = tmp_path / "alice.jsonl"
    pb = tmp_path / "bob.jsonl"
    la = Ledger(path=str(pa))
    lb = Ledger(path=str(pb))
    alice = Tenant(identity="alice")
    bob = Tenant(identity="bob")

    # Three exchanges: ASCII, Chinese, emoji
    payloads = [
        ("x1", {"doc": "ascii"}),
        ("x2", {"doc": "中文文档"}),
        ("x3", {"doc": "emoji 🎉"}),
    ]
    for xchg, p in payloads:
        book_send(alice, la, xchg=xchg, peer="bob", payload=p)
        book_receive(bob, lb, xchg=xchg, peer="alice", payload=p)
    la.close()
    lb.close()

    agreed, refused = agreed_state(str(pa), str(pb))
    assert len(agreed) == 3
    assert len(refused) == 0
    # Verify each minted exchange has the right payload hash
    for v, (_, p) in zip(agreed, payloads):
        assert v.payload_hash == payload_hash(p)


# ============================================================================
# Tamper-evident: changing non-ASCII content breaks the chain
# ============================================================================

def test_tamper_non_ascii_breaks_chain(tmp_path):
    """If non-ASCII content in a ledger is altered, verify() returns False."""
    p = tmp_path / "ledger.jsonl"
    ledger = Ledger(path=str(p))
    t = Tenant(identity="alice")
    bind(t, ledger, kind="task", payload={"task": "总结文档"})
    ledger.close()

    # Tamper: change 总结 to 总节 (different character)
    content = p.read_bytes()
    tampered = content.replace("总结".encode("utf-8"), "总节".encode("utf-8"))
    p.write_bytes(tampered)

    ledger2 = Ledger(path=str(p))
    assert not ledger2.verify(), "Chain should NOT verify after tampering"
