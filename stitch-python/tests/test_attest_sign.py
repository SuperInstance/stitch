"""
Tests for the opt-in signed ATTEST path (added on adoption of v0.3).

The v0.3 draft shipped a proven Ed25519 module (stitch.crypto) but never
wired it in: `attest` still wrote the v0.1 SHA-256 placeholder and the
audit checked no signatures. On adoption, the wiring was added in the
smallest default-off shape:

  - `attest(..., secret_key=<32-byte Ed25519 secret>)` writes a real
    RFC 8032 signature (128 hex chars) instead of the placeholder.
    Without the argument, the entry is byte-identical to v0.1/v0.2.
  - `audit_ledger(path, attest_keys={identity: public_key})` verifies
    such signatures and reports attest_verified / attest_unsigned /
    attest_failures. Without keys, no signature work happens.

What this does NOT claim: key-to-identity binding (out of band), or
signatures on any entry type other than ATTEST.
"""

import pytest

import stitch
from stitch import Tenant, Ledger, bind, attest, audit_ledger
from stitch.crypto import Key


def _ledger_with_attest(tmp_path, sign_with=None, identity_key=None):
    """A one-cell ledger with a single ATTEST entry; returns (path, key)."""
    key = identity_key or Key.generate()
    alice = Tenant(identity=key.identity)
    p = tmp_path / "alice.jsonl"
    ledger = Ledger(path=str(p))
    cell = bind(alice, ledger, kind="task", payload={"goal": "x"})
    if sign_with is None:
        sig = attest(alice, ledger, cell, claim="completed")
    else:
        sig = attest(alice, ledger, cell, claim="completed",
                     secret_key=sign_with)
    ledger.close()
    return p, key, sig


def test_default_attest_is_placeholder_and_counts_unsigned(tmp_path):
    """No key at write time → 16-hex placeholder, unchanged wire format;
    a keyed audit counts it as unsigned, not failed."""
    p, key, sig = _ledger_with_attest(tmp_path)
    assert len(sig) == 16

    verdict = audit_ledger(str(p), attest_keys={key.identity: key.public_bytes})
    assert verdict.ok
    assert verdict.attest_verified == 0
    assert verdict.attest_unsigned == 1
    assert verdict.attest_failures == []
    assert verdict.signatures_ok


def test_signed_attest_verifies_under_audit(tmp_path):
    """attest with a secret key → 128-hex Ed25519 sig; keyed audit verifies."""
    key = Key.generate()
    p, key, sig = _ledger_with_attest(
        tmp_path, sign_with=key.secret_bytes, identity_key=key)
    assert len(sig) == 128

    # Keys may be supplied as raw bytes or as hex (identity IS the hex).
    verdict = audit_ledger(str(p), attest_keys={key.identity: key.public_bytes})
    assert verdict.ok
    assert verdict.attest_verified == 1
    assert verdict.attest_unsigned == 0
    assert verdict.signatures_ok

    verdict_hex = audit_ledger(str(p), attest_keys={key.identity: key.identity})
    assert verdict_hex.attest_verified == 1


def test_signed_attest_with_wrong_key_fails_verification(tmp_path):
    """An attestation signed by an impostor key, recorded under the
    victim's identity, fails the keyed audit — while chain and law,
    separate axes, still verify."""
    victim = Key.generate()
    impostor = Key.generate()
    p, _, sig = _ledger_with_attest(
        tmp_path, sign_with=impostor.secret_bytes, identity_key=victim)
    assert len(sig) == 128

    verdict = audit_ledger(
        str(p), attest_keys={victim.identity: victim.public_bytes})
    assert verdict.chain_ok
    assert verdict.law_ok
    assert verdict.attest_verified == 0
    assert len(verdict.attest_failures) == 1
    assert not verdict.signatures_ok


def test_audit_without_keys_does_no_signature_work(tmp_path):
    """Default audit: signature fields stay at defaults even on a
    signed ledger — the opt-in is the auditor's, not ambient."""
    key = Key.generate()
    p, key, _ = _ledger_with_attest(
        tmp_path, sign_with=key.secret_bytes, identity_key=key)

    verdict = audit_ledger(str(p))
    assert verdict.ok
    assert verdict.attest_verified == 0
    assert verdict.attest_unsigned == 0
    assert verdict.attest_failures == []
    assert verdict.signatures_ok


def test_attest_rejects_wrong_length_secret_key(tmp_path):
    """A secret_key that is not 32 bytes is refused at write time."""
    key = Key.generate()
    alice = Tenant(identity=key.identity)
    ledger = Ledger(path=str(tmp_path / "a.jsonl"))
    cell = bind(alice, ledger, kind="task", payload={})
    with pytest.raises(ValueError):
        attest(alice, ledger, cell, claim="x", secret_key=b"short")
    ledger.close()
