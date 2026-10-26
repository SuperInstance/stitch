"""
Byte-exactness tests — verifies the wire format is deterministic.

This is the contract: identical inputs in any conformant implementation
must produce identical ledger bytes.
"""

import json
import os
import pytest
from pathlib import Path

import stitch
from stitch import Tenant, Ledger, KLSurprise, bind, effect, tick, attest
from stitch.core import FNV_OFFSET, FNV_PRIME, MASK_64


def test_fnv1a_known_vectors():
    """Verify FNV-1a 64-bit hash against known test vectors."""
    def fnv1a(data: str, seed: int = FNV_OFFSET) -> int:
        h = seed & MASK_64
        for b in data.encode("utf-8"):
            h ^= b
            h = (h * FNV_PRIME) & MASK_64
        return h

    # Known FNV-1a 64-bit vectors (seed = FNV_OFFSET)
    # Empty string → FNV_OFFSET
    assert fnv1a("") == FNV_OFFSET
    # "a" → 0xaf63dc4c8601ec8c
    assert fnv1a("a") == 0xaf63dc4c8601ec8c
    # "foobar" → 0x85944171f73967e8
    assert fnv1a("foobar") == 0x85944171f73967e8


def test_json_serialization_deterministic():
    """json.dumps with sorted keys + compact separators is deterministic."""
    d = {"b": 2, "a": 1, "c": [3, 2, 1]}
    s1 = json.dumps(d, separators=(",", ":"), sort_keys=True)
    s2 = json.dumps(d, separators=(",", ":"), sort_keys=True)
    assert s1 == s2
    # Keys should be sorted
    assert s1 == '{"a":1,"b":2,"c":[3,2,1]}'


def test_ledger_byte_format(tmp_path):
    """The first ledger entry has a specific byte format."""
    path = tmp_path / "state.jsonl"
    ledger = Ledger(path=str(path))
    ledger.append({"t": 1000, "ns": "alice", "op": "BIND", "cell": "abc", "g": 0.0})
    ledger.close()
    content = path.read_text().strip()
    # Should be valid JSON
    entry = json.loads(content)
    assert entry["t"] == 1000
    assert entry["ns"] == "alice"
    assert entry["op"] == "BIND"
    assert entry["cell"] == "abc"
    assert entry["g"] == 0.0
    assert "h" in entry
    assert len(entry["h"]) == 16
    # The hash should be the FNV-1a of the line without h, seeded with FNV_OFFSET
    line_without_h = json.dumps(
        {k: v for k, v in entry.items() if k != "h"},
        separators=(",", ":"), sort_keys=True
    )
    expected_h = 0xcbf29ce484222325
    for b in line_without_h.encode("utf-8"):
        expected_h ^= b
        expected_h = (expected_h * FNV_PRIME) & MASK_64
    assert entry["h"] == f"{expected_h:016x}"


def test_ledger_hash_chain_links_entries(tmp_path):
    """Each entry's hash is seeded with the previous entry's hash."""
    path = tmp_path / "state.jsonl"
    ledger = Ledger(path=str(path))
    h1 = ledger.append({"t": 1, "ns": "alice", "op": "BIND", "cell": "a", "g": 0.0})
    h2 = ledger.append({"t": 2, "ns": "alice", "op": "BIND", "cell": "b", "g": 0.0})
    ledger.close()
    # h1 should be the FNV-1a of the first entry seeded with FNV_OFFSET
    # h2 should be the FNV-1a of the second entry seeded with int(h1, 16)
    entries = []
    with open(path) as f:
        for line in f:
            entries.append(json.loads(line.strip()))

    # Verify h1
    e1 = {k: v for k, v in entries[0].items() if k != "h"}
    line1 = json.dumps(e1, separators=(",", ":"), sort_keys=True)
    expected_h1 = FNV_OFFSET
    for b in line1.encode("utf-8"):
        expected_h1 ^= b
        expected_h1 = (expected_h1 * FNV_PRIME) & MASK_64
    assert entries[0]["h"] == f"{expected_h1:016x}"
    assert entries[0]["h"] == h1

    # Verify h2 (seeded with h1)
    e2 = {k: v for k, v in entries[1].items() if k != "h"}
    line2 = json.dumps(e2, separators=(",", ":"), sort_keys=True)
    expected_h2 = int(h1, 16)
    for b in line2.encode("utf-8"):
        expected_h2 ^= b
        expected_h2 = (expected_h2 * FNV_PRIME) & MASK_64
    assert entries[1]["h"] == f"{expected_h2:016x}"
    assert entries[1]["h"] == h2


def test_deterministic_byte_output(tmp_path):
    """Identical input sequences (with mocked timestamps) produce byte-identical ledgers."""
    import stitch.stitches as stitches_mod
    original = stitches_mod._now_ns

    counter = [0]
    def fake_now():
        counter[0] += 1
        return counter[0] * 1_000_000_000

    paths = []
    try:
        for run in range(3):
            counter[0] = 0
            stitches_mod._now_ns = fake_now
            p = tmp_path / f"run_{run}.jsonl"
            paths.append(p)
            ledger = Ledger(path=str(p))
            t = Tenant(identity="alice")
            cell = bind(t, ledger, kind="task", payload={"goal": "test"})
            s = KLSurprise()
            effect(t, ledger, s, cell, "k", {"k": "a"}, {"k": "b"})
            tick(t, ledger)
            ledger.close()
    finally:
        stitches_mod._now_ns = original

    # All three runs should produce identical bytes
    bytes_0 = paths[0].read_bytes()
    bytes_1 = paths[1].read_bytes()
    bytes_2 = paths[2].read_bytes()
    assert bytes_0 == bytes_1 == bytes_2
