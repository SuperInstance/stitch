"""
stitch.crypto — opt-in cryptographic signatures for stitch.

Subpackage providing Ed25519 (RFC 8032) signatures, implemented in pure
Python using only the standard library (`hashlib.sha512`). Zero external
dependencies. The core `stitch` package remains stdlib-only; this
subpackage is opt-in — users who don't need signatures never import it.

Why it exists:
  v0.2's `attest` used a SHA-256 placeholder signature. v0.3 adds this
  module plus an opt-in signed path: `attest` writes a real signature
  when given a secret key, and `audit_ledger` verifies signatures when
  given the tenants' public keys (`attest_keys`). Both default off.
  This does not by itself bind a key to an identity — that binding is
  still out of band — so treat "copy authenticity" as enforced only
  where a writer opted in and an auditor supplied keys.

Public API:
    Key           — wraps an Ed25519 keypair (signing + verifying)
    sign, verify  — bare functions
    secret_key_bytes — generate a random secret key
    public_key    — derive the public key from a secret key
"""

from .ed25519 import (
    # Constants
    P, L, D, I, B, IDENTITY,
    # Functions
    secret_key_bytes,
    public_key,
    sign,
    verify,
    # Class
    Key,
)

__all__ = [
    "Key",
    "sign",
    "verify",
    "secret_key_bytes",
    "public_key",
]
