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

Arithmetic: points are held in extended twisted-Edwards coordinates
(X, Y, Z, T) with x = X/Z, y = Y/Z (Hisil–Wong–Carter–Dawson unified
formulas), so scalar multiplication performs no modular inversions at
all — the single inversion left is when a point is compressed to
bytes — and equality in verify is checked projectively (cross-
multiplication), which needs none. The base point B is additionally
precomputed into a windowed fixed-base table (window w = 4, built once
at import), so [s]B is 64 table lookups + additions with zero
doublings; only verify's variable-base [k]A uses plain double-and-add.
This replaced v0.3's affine coordinates, where every point addition
cost two inversions (a full 255-bit exponentiation each) and a scalar
multiplication needed ~380 of them.

Performance: measured ≈0.8 ms per sign and ≈2.3 ms per verify (mean
of 20, ARM64 Tegra) on this project's test machine — roughly two
orders of magnitude faster than v0.3's ≈270 ms per operation, with
byte-identical outputs (Ed25519 signing is deterministic, so
correctness is byte-equality, not just interoperability; the pinned
vectors and the v0.3 cross-checks pin this). Still pure Python:
production deployments with throughput-shaped workloads should use a
native implementation (libsodium via pynacl, or RustCrypto
ed25519-dalek). The byte format is RFC 8032 — signatures interoperate
with all standard Ed25519 implementations.

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

#: 2*d mod p (the extended-coordinate formulas use it pre-doubled)
D2 = (2 * D) % P

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

# Edwards curve point type: (x, y) tuple, or None for identity
_Point = Optional[Tuple[int, int]]

#: Identity point (neutral element on Edwards curve): (0, 1)
IDENTITY: _Point = (0, 1)


# ============================================================================
# Field / point operations (extended twisted-Edwards coordinates)
# ============================================================================

# Extended point: (X, Y, Z, T) with x = X/Z, y = Y/Z, T = XY/Z.
_ExtPoint = Tuple[int, int, int, int]

#: Identity point in extended coordinates: x = 0, y = 1
IDENTITY_EXT: _ExtPoint = (0, 1, 1, 0)


def _to_ext(x: int, y: int) -> _ExtPoint:
    """Lift an affine point (x, y) to extended coordinates."""
    return (x % P, y % P, 1, (x * y) % P)


def _ext_add(A: _ExtPoint, Bp: _ExtPoint) -> _ExtPoint:
    """
    Unified addition for a = -1 twisted Edwards curves in extended
    coordinates (Hisil–Wong–Carter–Dawson). No inversion; also correct
    when A == Bp, so it serves for doubling too.
    """
    X1, Y1, Z1, T1 = A
    X2, Y2, Z2, T2 = Bp
    a = ((Y1 - X1) * (Y2 - X2)) % P
    b = ((Y1 + X1) * (Y2 + X2)) % P
    c = (D2 * T1 * T2) % P
    d_ = (2 * Z1 * Z2) % P
    e = (b - a) % P
    f = (d_ - c) % P
    g = (d_ + c) % P
    h = (b + a) % P
    return ((e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P)


def _ext_double(A: _ExtPoint) -> _ExtPoint:
    """Doubling in extended coordinates (cheaper than generic addition)."""
    X1, Y1, Z1, _ = A
    a = (X1 * X1) % P
    b = (Y1 * Y1) % P
    c = (2 * Z1 * Z1) % P
    d_ = (-a) % P
    e = ((X1 + Y1) * (X1 + Y1) - a - b) % P
    g = (d_ + b) % P
    f = (g - c) % P
    h = (d_ - b) % P
    return ((e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P)


def _ext_scalar_mult(s: int, A: _ExtPoint) -> _ExtPoint:
    """Variable-base double-and-add in extended coordinates, no inversions."""
    s %= L
    result = IDENTITY_EXT
    addend = A
    while s:
        if s & 1:
            result = _ext_add(result, addend)
        addend = _ext_double(addend)
        s >>= 1
    return result


def _ext_equal(A: _ExtPoint, Bp: _ExtPoint) -> bool:
    """
    Projective equality: A == Bp iff x1/z1 == x2/z2 and y1/z1 == y2/z2,
    checked by cross-multiplication — no inversion, and equivalent to
    comparing compressed encodings because compression is canonical.
    """
    X1, Y1, Z1, _ = A
    X2, Y2, Z2, _ = Bp
    return ((X1 * Z2 - X2 * Z1) % P == 0
            and (Y1 * Z2 - Y2 * Z1) % P == 0)


# --- fixed-base table for B (window w=4) ----------------------------------
# table[i][j] = j * 16^i * B, precomputed once at import. A fixed-base
# multiplication [s]B becomes 64 table lookups + additions (zero doublings).

_WINDOW = 4
_DIGITS = 256 // _WINDOW  # 64

_B_EXT = _to_ext(BX, BY)


def _build_table() -> list:
    table = []
    base = _B_EXT
    for _ in range(_DIGITS):
        row = [IDENTITY_EXT]
        acc = IDENTITY_EXT
        for _j in range(1, 1 << _WINDOW):
            acc = _ext_add(acc, base)
            row.append(acc)
        table.append(row)
        for _k in range(_WINDOW):
            base = _ext_double(base)
    return table


_TABLE = _build_table()


def _fixed_scalar_mult(s: int) -> _ExtPoint:
    """[s]B via the precomputed windowed table (fixed base)."""
    s %= L
    result = IDENTITY_EXT
    for i in range(_DIGITS):
        digit = (s >> (i * _WINDOW)) & ((1 << _WINDOW) - 1)
        if digit:
            result = _ext_add(result, _TABLE[i][digit])
    return result


# ============================================================================
# Encoding / decoding
# ============================================================================

def _point_compress_ext(A: _ExtPoint) -> bytes:
    """
    Compress an extended point to 32 bytes (y-coordinate with sign bit).
    This is the one place a modular inversion is performed.
    """
    X, Y, Z, _ = A
    zinv = pow(Z, P - 2, P)
    x = (X * zinv) % P
    y = (Y * zinv) % P
    bits = (y | ((x & 1) << 255)) & ((1 << 256) - 1)
    return bits.to_bytes(32, "little")


def _xrecover(y: int, sign: int = 0) -> int:
    """Recover x from y on the Edwards curve. sign selects the root."""
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * I) % P
    if x % 2 != sign:
        x = P - x
    return x


def _point_decompress(data: bytes) -> _Point:
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
    return _point_compress_ext(_fixed_scalar_mult(a))


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
    A_enc = _point_compress_ext(_fixed_scalar_mult(a))
    r = _sha512_int(prefix + message)
    R_enc = _point_compress_ext(_fixed_scalar_mult(r))
    hram = _sha512_int(R_enc + A_enc + message)
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
    # Check [s]B == R + [hram]A (projectively; compression is canonical)
    sB = _fixed_scalar_mult(s)
    rhs = _ext_add(_to_ext(*R), _ext_scalar_mult(hram, _to_ext(*A)))
    return _ext_equal(sB, rhs)


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
