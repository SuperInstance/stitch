"""
Numeric-policy enforcement at booking time (backend tightening).

SPEC.md's writer policy has always said non-finite floats MUST NOT be
booked; until now the booking helpers booked NaN/Infinity happily and
only the policy text objected — spec/library drift. These tests pin
the fix and its exact boundaries:

  - book_send / book_receive raise ValueError on any non-finite float
    in the payload — top-level, nested in dicts, nested in lists;
  - the refusal happens BEFORE signing or any ledger write: the
    ledger file is byte-identical afterwards and nothing is appended
    (the append-only refusal invariant);
  - finite floats — including the policy's own edge cases 1e-05,
    -0.0 and 1e308 — still book and mint normally;
  - the read side is deliberately NOT policed: payload_hash hashes a
    NaN-containing payload exactly as it always has. Writers are
    policed; readers return verdicts, never crash on foreign content.
"""
import hashlib

import pytest

import stitch
from stitch import Tenant, Ledger, book_send, book_receive, agree
from stitch.agreement import payload_hash

NAN = float("nan")
INF = float("inf")
SK = bytes([7] * 32)  # fixed test key (published on purpose, as elsewhere)


def _pair(tmp_path):
    pa, pb = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    alice, bob = Tenant(identity="alice"), Tenant(identity="bob")
    return pa, pb, alice, bob


def _bytes(path, tenant, fn):
    """Run fn on a fresh Ledger over `path`; return the file bytes after."""
    led = Ledger(path=str(path))
    try:
        fn(tenant, led)
    finally:
        led.close()
    return path.read_bytes()


def test_top_level_nan_refused_by_both_helpers(tmp_path):
    pa, pb, alice, bob = _pair(tmp_path)
    for book, tenant, path, peer in (
            (book_send, alice, pa, "bob"), (book_receive, bob, pb, "alice")):
        led = Ledger(path=str(path))
        with pytest.raises(ValueError):
            book(tenant, led, xchg="x1", peer=peer, payload=NAN)
        led.close()
        assert path.read_bytes() == b""  # nothing was ever written


def test_nan_nested_in_dict_refused(tmp_path):
    pa, _, alice, _ = _pair(tmp_path)
    led = Ledger(path=str(pa))
    with pytest.raises(ValueError):
        book_send(alice, led, xchg="x1", peer="bob",
                  payload={"reading": {"value": NAN, "unit": "m"}})
    led.close()
    assert pa.read_bytes() == b""


def test_negative_infinity_nested_in_list_refused(tmp_path):
    pa, _, alice, _ = _pair(tmp_path)
    led = Ledger(path=str(pa))
    with pytest.raises(ValueError):
        book_receive(alice, led, xchg="x1", peer="bob",
                     payload={"samples": [1.0, 2.5, -INF]})
    led.close()
    assert pa.read_bytes() == b""


def test_refusal_leaves_ledger_byte_identical(tmp_path):
    pa, _, alice, _ = _pair(tmp_path)
    before = _bytes(pa, alice, lambda t, l: book_send(
        t, l, xchg="good", peer="bob", payload={"v": 1.5}))
    assert before  # the ledger has a real booking in it

    def bad(t, l):
        for payload in (NAN, {"a": [INF]}, {"a": {"b": NAN}}):
            with pytest.raises(ValueError):
                book_send(t, l, xchg="bad", peer="bob", payload=payload)
            with pytest.raises(ValueError):
                book_receive(t, l, xchg="bad", peer="bob", payload=payload)

    after = _bytes(pa, alice, bad)
    assert after == before  # not one byte moved
    assert len(after.decode().strip().splitlines()) == \
        len(before.decode().strip().splitlines())  # nothing appended


def test_signed_booking_refuses_non_finite_before_signing(tmp_path):
    pa, _, alice, _ = _pair(tmp_path)
    before = _bytes(pa, alice, lambda t, l: book_send(
        t, l, xchg="good", peer="bob", payload={"v": 3}, secret_key=SK))
    led = Ledger(path=str(pa))
    with pytest.raises(ValueError):
        book_send(alice, led, xchg="bad", peer="bob",
                  payload={"v": INF}, secret_key=SK)
    led.close()
    assert pa.read_bytes() == before


def test_finite_edge_floats_book_and_mint(tmp_path):
    pa, pb, alice, bob = _pair(tmp_path)
    payload = {"small": 1e-05, "negzero": -0.0, "huge": 1e308, "n": 7}
    la, lb = Ledger(path=str(pa)), Ledger(path=str(pb))
    book_send(alice, la, xchg="x1", peer="bob", payload=payload)
    book_receive(bob, lb, xchg="x1", peer="alice", payload=payload)
    la.close(); lb.close()
    v = agree(str(pa), str(pb), "x1")
    assert v.agreed, v.reason


def test_payload_hash_still_hashes_nan_payload_as_before():
    # The deliberate asymmetry, pinned: the read side is not policed.
    # payload_hash keeps its v0.4 behaviour — canonical json.dumps
    # (allow_nan default) — so foreign/legacy payloads still hash.
    expected = hashlib.sha256(b'{"v":NaN}').hexdigest()
    assert payload_hash({"v": NAN}) == expected
    assert payload_hash({"v": NAN}) == payload_hash({"v": float("nan")})
