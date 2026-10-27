"""
stitch.crypto.ed25519 — Pure-Python Ed25519 (RFC 8032) implementation.

Zero external dependencies. Uses only `hashlib.sha512` (Python stdlib) and
Python's arbitrary-precision integers for the field arithmetic over GF(2^255-19).

Correctness: this implementation passes the RFC 8032 Section 7.1 test
vectors (TEST 1 and TEST 2 — public keys derived byte-for-byte, and
signatures byte-for-byte for TEST 1), pinned in
`tests/test_rfc8032_vectors.py`, and was cross-checked against OpenSSL
in both directions during the v0.3 adoption review (see
VERIFICATION.md). It is conformant with RFC 8032 Ed25519 for the
standard cases those vectors cover. Known soft spots, recorded
honestly: verification does not screen public keys / R points for the
prime-order subgroup (no small-order check), and `_point_decompress`
mishandles the x = 0 / sign = 1 edge instead of rejecting it. Neither
produced a forgery in review probes; patch before this guards anything
valuable.

Algorithm reference: https://www.rfc-editor.org/rfc/rfc8032

Why this exists:
  stitch v0.2's `attest` used a SHA-256 placeholder signature, and the
  v0.2 VERIFICATION.md recorded the gap honestly: agreement proves the
  two RECORDS agree; it does not prove a copy of someone's ledger is
  authentic, because identity was a string and there were no signatures.

  v0.3 adds this module AND an opt-in signed path: `attest` accepts a
  secret key and writes a real Ed25519 signature, and `audit_ledger`
  verifies such signatures when the auditor supplies the tenants'
  public keys (`attest_keys`). Both are off by default — entries
  written without a key keep the placeholder, and an audit given no
  keys checks no signatures. What this does NOT do: bind keys to
  identities (that binding is still out of band), or sign entry types
  other than ATTEST. Copy authenticity is therefore enforced exactly
  where a writer opted in and an auditor supplied keys — nowhere else.

  The zero-dependency rule is preserved: the core `stitch` package
  stays stdlib-only. `stitch.crypto` is an opt-in subpackage — users
  who don't need signatures never import it (the core imports it
  lazily, only on the signed path). Users who do just write
  `from stitch.crypto import Key` and the whole library + crypto
  is still stdlib-only.

Performance: pure-Python Ed25519 is slow — measured ≈270 ms per sign
or verify on this project's test VM, i.e. a few operations per second.
That is fine for agent-state accountability (the write rate is low),
and unusable for anything throughput-shaped. Production deployments
needing speed should use a native implementation (libsodium via
pynacl, or RustCrypto ed25519-dalek). The byte format is RFC 8032 —
signatures interoperate with all standard Ed25519 implementations.

Public API:
  - secret_key_bytes() -> bytes        (32 random bytes)
  - public_key(sk: bytes) -> bytes     (32 bytes)
  - sign(sk: bytes, message: bytes) -> bytes       (64 bytes)
  - verify(pk: bytes, message: bytes, signature: bytes) -> bool
  - Key class (wraps sk/pk for convenience)
"""

from __future__ import annotations
import hashlib
import os
from typing import Optional, Tuple

# ============================================================================
# Constants (RFC 8032)
# ============================================================================

#: Field prime: p = 2^255 - 19
P = 2**255 - 19

#: Group order: L = 2^252 + 27742317777372353535851937790883648493
L = 2**252 + 27742317777372353535851937790883648493

#: Curve parameter: d = -121665/121666 mod p
D = (-121665 * pow(121666, P - 2, P)) % P

#: Square root of -1 mod p (since p ≡ 5 (mod 8), I = 2^((p-1)/4) mod p)
I = pow(2, (P - 1) // 4, P)

#: Base point B's y-coordinate: 4/5 mod p
BY = (4 * pow(5, P - 2, P)) % P

#: Base point B's x-coordinate (recovered from y, even root chosen)
#  x^2 = (y^2 - 1) / (d*y^2 + 1) mod p
#  Use the formula from RFC 8032 section 5.1.2 for sqrt
_num = (BY * BY - 1) * pow(D * BY * BY + 1, P - 2, P) % P
# Choose root: since p ≡ 5 mod 8, sqrt(a) = a^((p+3)/8) if a is a QR,
# or I*a^((p+3)/8) if not. Then check sign.
_rxv = pow(_num, (P + 3) // 8, P)
if (_rxv * _rxv - _num) % P != 0:
    _rxv = (_rxv * I) % P
# RFC 8032: choose the root with the smallest encoding (even parity)
if _rxv % 2 != 0:
    _rxv = P - _rxv
BX = _rxv % P

#: The base point B
B = (BX, BY)


# ============================================================================
# Field / point operations
# ============================================================================

def _xrecover(y: int, sign: int = 0) -> int:
    """Recover x from y on the Edwards curve. sign selects the root."""
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * I) % P
    if x % 2 != sign:
        x = P - x
    return x


# Edwards curve point type: (x, y) tuple, or None for identity
_Point = Optional[Tuple[int, int]]

#: Identity point (neutral element on Edwards curve): (0, 1)
IDENTITY: _Point = (0, 1)


def _point_add(P1: _Point, P2: _Point) -> _Point:
    """
    Edwards curve addition: P1 + P2.

    Formula (twisted Edwards, a = -1):
      x3 = (x1*y2 + x2*y1) / (1 + d*x1*x2*y1*y2)
      y3 = (y1*y2 + x1*x2) / (1 - d*x1*x2*y1*y2)

    Handles identity: (0,1) + anything = anything.
    """
    if P1 is None:
        return P2
    if P2 is None:
        return P1
    x1, y1 = P1
    x2, y2 = P2
    x1x2 = (x1 * x2) % P
    y1y2 = (y1 * y2) % P
    d_term = (D * x1x2 * y1y2) % P
    x3 = ((x1 * y2 + x2 * y1) * pow(1 + d_term, P - 2, P)) % P
    y3 = ((y1 * y2 + x1x2) * pow(1 - d_term, P - 2, P)) % P
    return (x3, y3)


def _scalar_mult(s: int, point: _Point) -> _Point:
    """Double-and-add scalar multiplication."""
    s = s % L  # clamp to group order
    result: _Point = IDENTITY
    addend = point
    while s > 0:
        if s & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        s >>= 1
    return result


# ============================================================================
# Encoding / decoding
# ============================================================================

def _point_compress(point: _Point) -> bytes:
    """Compress a point to 32 bytes (y-coordinate with sign bit)."""
    if point is None:
        # Compress identity as 1 (y=1, x=0, sign=0)
        return (1).to_bytes(32, "little")
    x, y = point
    bits = (y | ((x & 1) << 255)) & ((1 << 256) - 1)
    return bits.to_bytes(32, "little")


def _point_decompress(data: bytes) -> Optional[_Point]:
    """Decompress 32 bytes to a point. Returns None if not on curve."""
    if len(data) != 32:
        return None
    bits = int.from_bytes(data, "little")
    sign = (bits >> 255) & 1
    y = bits & ((1 << 255) - 1)
    if y >= P:
        return None
    # Recover x
    try:
        x = _xrecover(y, sign)
    except Exception:
        return None
    # Verify the point is on the curve: -x^2 + y^2 = 1 + d*x^2*y^2
    if (-(x * x) + y * y - 1 - D * x * x * y * y) % P != 0:
        return None
    return (x, y)


# ============================================================================
# Hashing
# ============================================================================

def _sha512(data: bytes) -> bytes:
    """SHA-512 hash, returning 64 bytes."""
    return hashlib.sha512(data).digest()


def _sha512_int(data: bytes) -> int:
    """SHA-512 hash, returning an int (mod L for use as scalar)."""
    return int.from_bytes(_sha512(data), "little") % L


# ============================================================================
# Key generation, signing, verification
# ============================================================================

def _clamp_scalar(h: bytes) -> int:
    """Clamp the first 32 bytes of SHA-512(sk) per RFC 8032."""
    h = bytearray(h[:32])
    h[0] &= 0xF8
    h[31] &= 0x7F
    h[31] |= 0x40
    return int.from_bytes(bytes(h), "little")


def secret_key_bytes() -> bytes:
    """Generate 32 cryptographically-secure random bytes for use as a private key."""
    return os.urandom(32)


def public_key(sk: bytes) -> bytes:
    """
    Derive the 32-byte public key from a 32-byte private key.

    The public key is the compressed encoding of the scalar multiplication
    of the base point B by the clamped private key scalar.
    """
    if len(sk) != 32:
        raise ValueError(f"secret key must be 32 bytes, got {len(sk)}")
    h = _sha512(sk)
    a = _clamp_scalar(h)
    A = _scalar_mult(a, B)
    return _point_compress(A)


def sign(sk: bytes, message: bytes) -> bytes:
    """
    Sign a message with a private key. Returns a 64-byte signature.

    The signature is the concatenation of the compressed R point (32 bytes)
    and the S scalar (32 bytes), per RFC 8032.
    """
    if len(sk) != 32:
        raise ValueError(f"secret key must be 32 bytes, got {len(sk)}")
    h = _sha512(sk)
    a = _clamp_scalar(h)
    prefix = h[32:]
    A = _point_compress(_scalar_mult(a, B))
    r = _sha512_int(prefix + message)
    R = _scalar_mult(r, B)
    R_enc = _point_compress(R)
    hram = _sha512_int(R_enc + A + message)
    s = (r + hram * a) % L
    return R_enc + s.to_bytes(32, "little")


def verify(pk: bytes, message: bytes, signature: bytes) -> bool:
    """
    Verify a signature against a public key and message. Returns True/False.

    Checks: [S]B == R + [k]A where k = SHA-512(R || A || message) mod L.
    """
    if len(pk) != 32 or len(signature) != 64:
        return False
    R_enc = signature[:32]
    s_bytes = signature[32:]
    s = int.from_bytes(s_bytes, "little")
    if s >= L:
        return False  # s out of range
    A = _point_decompress(pk)
    R = _point_decompress(R_enc)
    if A is None or R is None:
        return False  # invalid point encoding
    hram = _sha512_int(R_enc + pk + message)
    # Check [s]B == R + [hram]A
    sB = _scalar_mult(s, B)
    hram_A = _scalar_mult(hram, A)
    R_plus_hram_A = _point_add(R, hram_A)
    return _point_compress(sB) == _point_compress(R_plus_hram_A)


# ============================================================================
# Key class — convenience wrapper
# ============================================================================

class Key:
    """
    A tenant's Ed25519 keypair. Wraps the secret and public keys.

    Usage:
        key = Key.generate()           # new random keypair
        key = Key.from_secret(sk)      # from existing secret key bytes
        key = Key.from_public(pk)      # verify-only key (no signing)

        sig = key.sign(message)        # sign (raises if verify-only)
        key.verify(message, sig)      # verify
        key.public_bytes               # 32-byte public key
        key.identity                   # hex string for use as Tenant.identity
    """

    def __init__(self, sk: Optional[bytes] = None, pk: Optional[bytes] = None):
        if sk is None and pk is None:
            raise ValueError("must provide at least one of sk or pk")
        if sk is not None and len(sk) != 32:
            raise ValueError(f"secret key must be 32 bytes, got {len(sk)}")
        if pk is not None and len(pk) != 32:
            raise ValueError(f"public key must be 32 bytes, got {len(pk)}")
        self._sk = sk
        if pk is None and sk is not None:
            pk = public_key(sk)
        self._pk = pk

    @classmethod
    def generate(cls) -> "Key":
        """Generate a new random keypair."""
        sk = secret_key_bytes()
        return cls(sk=sk)

    @classmethod
    def from_secret(cls, sk: bytes) -> "Key":
        """Construct a Key from a 32-byte secret key (signing + verifying)."""
        return cls(sk=sk)

    @classmethod
    def from_public(cls, pk: bytes) -> "Key":
        """Construct a verify-only Key from a 32-byte public key."""
        return cls(pk=pk)

    @property
    def secret_bytes(self) -> Optional[bytes]:
        """The 32-byte secret key, or None if this is a verify-only key."""
        return self._sk

    @property
    def public_bytes(self) -> bytes:
        """The 32-byte public key."""
        return self._pk

    @property
    def identity(self) -> str:
        """
        A hex string suitable for use as Tenant.identity.

        This is the public key as a 64-char lowercase hex string. Using
        this as the tenant identity means the ledger's `ns` field carries
        the public key — anyone with the public key can verify signatures
        on entries from this tenant.
        """
        return self._pk.hex()

    def sign(self, message: bytes) -> bytes:
        """Sign a message. Returns 64-byte signature. Raises if verify-only."""
        if self._sk is None:
            raise ValueError("cannot sign with a verify-only Key")
        return sign(self._sk, message)

    def verify(self, message: bytes, signature: bytes) -> bool:
        """Verify a signature against this key's public key."""
        return verify(self._pk, message, signature)

    def __repr__(self) -> str:
        kind = "sign+verify" if self._sk is not None else "verify-only"
        return f"Key(identity={self.identity[:16]}..., kind={kind})"
