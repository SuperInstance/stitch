"""
Tests for stitch.crypto.ed25519 — pure-Python Ed25519 (RFC 8032).

What's verified here:
  - Sign-then-verify round-trips (every sign() verifies under verify())
  - Negative tests (tampered messages, wrong keys, short sigs)
  - Determinism (Ed25519 is deterministic per RFC 8032)
  - Key class (generate, from_secret, from_public, identity)
  - Stitch integration (Key.identity as tenant identity; signed attestation
    that a third party can verify from the public key alone)

What's verified elsewhere:
  - Byte-exact compatibility with RFC 8032 test vectors from Section
    7.1: `tests/test_rfc8032_vectors.py` pins TEST 1 and TEST 2
    (derived public keys and signatures byte-for-byte). Added during
    the v0.3 adoption review, which also cross-checked this
    implementation against OpenSSL in both directions (see
    VERIFICATION.md). The vectors pass; the implementation is
    RFC-conformant for the cases they cover.

Performance note: pure-Python Ed25519 is slow (~10-50ms per sign/verify).
These tests take a few seconds; that's expected.
"""

import pytest

from stitch.crypto import (
    Key, sign, verify, secret_key_bytes, public_key,
)
from stitch.crypto.ed25519 import B, BX, BY, P, L


# ============================================================================
# Sign-then-verify round-trip (the minimum correctness check)
# ============================================================================

def test_sign_verify_round_trip():
    """For any key, sign(m) verifies under verify(pk, m, sig)."""
    sk = secret_key_bytes()
    pk = public_key(sk)
    msg = b"hello world"
    sig = sign(sk, msg)
    assert len(sig) == 64
    assert verify(pk, msg, sig)


def test_sign_verify_round_trip_empty_message():
    """Empty message should sign and verify."""
    sk = secret_key_bytes()
    pk = public_key(sk)
    sig = sign(sk, b"")
    assert verify(pk, b"", sig)


def test_sign_verify_round_trip_long_message():
    """Long message should sign and verify."""
    sk = secret_key_bytes()
    pk = public_key(sk)
    msg = b"a" * 1024
    sig = sign(sk, msg)
    assert verify(pk, msg, sig)


def test_sign_verify_round_trip_unicode_message():
    """Message with non-ASCII bytes should sign and verify."""
    sk = secret_key_bytes()
    pk = public_key(sk)
    msg = "héllo 世界 🌍".encode("utf-8")
    sig = sign(sk, msg)
    assert verify(pk, msg, sig)


# ============================================================================
# Negative tests (verification should fail)
# ============================================================================

def test_verify_rejects_tampered_message():
    """A signature on m should not verify for m'."""
    sk = secret_key_bytes()
    pk = public_key(sk)
    msg = b"hello"
    sig = sign(sk, msg)
    assert not verify(pk, b"goodbye", sig)


def test_verify_rejects_tampered_signature():
    """Flipping a bit in the signature should fail verification."""
    sk = secret_key_bytes()
    pk = public_key(sk)
    msg = b"hello"
    sig = bytearray(sign(sk, msg))
    sig[10] ^= 1
    assert not verify(pk, msg, bytes(sig))


def test_verify_rejects_wrong_public_key():
    """A signature under sk1 should not verify under pk2."""
    sk1 = secret_key_bytes()
    pk1 = public_key(sk1)
    sk2 = secret_key_bytes()
    pk2 = public_key(sk2)
    msg = b"hello"
    sig = sign(sk1, msg)
    assert verify(pk1, msg, sig)
    assert not verify(pk2, msg, sig)


def test_verify_rejects_short_signature():
    """A 63-byte signature should fail (must be 64 bytes)."""
    sk = secret_key_bytes()
    pk = public_key(sk)
    sig = sign(sk, b"hello")
    assert not verify(pk, b"hello", sig[:63])


def test_verify_rejects_short_public_key():
    """A 31-byte public key should fail (must be 32 bytes)."""
    sk = secret_key_bytes()
    pk = public_key(sk)
    sig = sign(sk, b"hello")
    assert not verify(pk[:31], b"hello", sig)


# ============================================================================
# Determinism — same key + same message → same signature (Ed25519 property)
# ============================================================================

def test_signing_is_deterministic():
    """Ed25519 is deterministic (RFC 8032). Same sk + msg → same sig."""
    sk = secret_key_bytes()
    msg = b"deterministic test"
    sig1 = sign(sk, msg)
    sig2 = sign(sk, msg)
    assert sig1 == sig2


def test_signing_differs_across_keys():
    """Different keys produce different signatures on the same message."""
    sk1 = secret_key_bytes()
    sk2 = secret_key_bytes()
    msg = b"same message"
    sig1 = sign(sk1, msg)
    sig2 = sign(sk2, msg)
    assert sig1 != sig2


# ============================================================================
# Curve constants sanity
# ============================================================================

def test_base_point_is_on_curve():
    """The base point B should be on the curve -x^2 + y^2 = 1 + d*x^2*y^2."""
    from stitch.crypto.ed25519 import D
    lhs = (-BX * BX + BY * BY) % P
    rhs = (1 + D * BX * BX * BY * BY) % P
    assert lhs == rhs


def test_field_prime_is_5_mod_8():
    """p ≡ 5 (mod 8) — required for the sqrt algorithm to work."""
    assert P % 8 == 5


def test_i_squared_is_minus_one():
    """I = sqrt(-1) mod p, so I^2 ≡ -1 (mod p)."""
    from stitch.crypto.ed25519 import I
    assert (I * I + 1) % P == 0


def test_base_point_y_is_4_over_5():
    """By = 4/5 mod p, so 5*By ≡ 4 (mod p)."""
    assert (5 * BY) % P == 4


# ============================================================================
# Key class
# ============================================================================

def test_key_generate_creates_signable_key():
    key = Key.generate()
    assert key.secret_bytes is not None
    assert len(key.public_bytes) == 32
    sig = key.sign(b"test message")
    assert key.verify(b"test message", sig)


def test_key_from_secret_preserves_public():
    sk = secret_key_bytes()
    key = Key.from_secret(sk)
    assert key.public_bytes == public_key(sk)
    assert key.identity == public_key(sk).hex()


def test_key_from_public_is_verify_only():
    sk = secret_key_bytes()
    pk = public_key(sk)
    key = Key.from_public(pk)
    assert key.secret_bytes is None
    assert key.public_bytes == pk
    with pytest.raises(ValueError, match="verify-only"):
        key.sign(b"message")


def test_key_identity_is_64_hex_chars():
    key = Key.generate()
    identity = key.identity
    assert len(identity) == 64
    int(identity, 16)  # raises if not hex


def test_key_round_trip_via_identity():
    """Two parties can communicate using just the identity (public key hex)."""
    signer = Key.generate()
    identity = signer.identity  # public key as hex string
    msg = b"signed by the signer"

    # Signer signs
    sig = signer.sign(msg)

    # Verifier reconstructs the public key from the identity alone
    pk_from_identity = bytes.fromhex(identity)
    assert verify(pk_from_identity, msg, sig)
    assert not verify(pk_from_identity, b"tampered", sig)


# ============================================================================
# Stitch integration: use Key.identity as tenant identity, sign attestations
# ============================================================================

def test_stitch_tenant_with_ed25519_identity():
    """A stitch Tenant can use a Key's identity (public key hex) as its identity string."""
    import stitch
    key = Key.generate()
    alice = stitch.Tenant(identity=key.identity)
    assert alice.identity == key.identity
    assert len(alice.identity) == 64


def test_signed_attestation_round_trip(tmp_path):
    """
    End-to-end: a tenant signs an attestation with their Ed25519 key,
    and a third party can verify the signature using the tenant's
    identity (which IS the public key).

    This closes the v0.2 "copy authenticity" gap: a forged ledger that
    claims to be from Alice would lack Alice's signature on the
    attested cell, so the auditor (who knows Alice's public key) can
    reject it.
    """
    import stitch
    from stitch import Tenant, Ledger, bind, attest

    alice_key = Key.generate()
    alice = Tenant(identity=alice_key.identity)
    ledger = Ledger(path=str(tmp_path / "alice.jsonl"))
    cell = bind(alice, ledger, kind="task", payload={"goal": "do something"})

    # The "attest" stitch records a SHA-256 placeholder sig in the ledger.
    # For real Ed25519, the caller signs the cell_id + claim with their key
    # and stores the sig OUT OF BAND. Anyone can verify using the public key
    # (which IS the tenant identity in the ledger).
    claim = "completed_task"
    attestation_material = f"{cell.id}:{claim}".encode("utf-8")
    real_sig = alice_key.sign(attestation_material)

    # A third party (auditor) reconstructs the public key from the tenant
    # identity (recorded in the ledger's `ns` field) and verifies the sig
    pk_from_identity = bytes.fromhex(alice.identity)
    assert verify(pk_from_identity, attestation_material, real_sig)

    # If we tamper with the claim, verification should fail
    assert not verify(
        pk_from_identity,
        f"{cell.id}:other_claim".encode("utf-8"),
        real_sig,
    )

    # If someone forges a ledger claiming to be alice but using a different
    # key, the signature won't verify against alice's real public key
    impostor_key = Key.generate()
    impostor_sig = impostor_key.sign(attestation_material)
    assert not verify(pk_from_identity, attestation_material, impostor_sig)
