"""
RFC 8032 Section 7.1 test vectors for stitch.crypto.ed25519.

The v0.3 draft's crypto module claimed conformance but shipped no
authoritative vectors — its own test file said so. These are the
official vectors from RFC 8032 §7.1 (TEST 1 and TEST 2), added during
the v0.3 adoption review so the conformance claim is pinned by tests,
not prose. The adoption assessment (VERIFICATION.md, v0.3) additionally
cross-checked this implementation against OpenSSL in both directions.

Source: https://www.rfc-editor.org/rfc/rfc8032#section-7.1
"""

from stitch.crypto import public_key, sign, verify

# RFC 8032 §7.1, TEST 1 — empty message.
TEST1_SK = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc4"
    "4449c5697b326919703bac031cae7f60")
TEST1_PK = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a"
    "0ee172f3daa62325af021a68f707511a")
TEST1_MSG = b""
TEST1_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")

# RFC 8032 §7.1, TEST 2 — one-octet message (0x72).
TEST2_SK = bytes.fromhex(
    "4ccd089b28ff96da9db6c346ec114e0f"
    "5b8a319f35aba624da8cf6ed4fb8a6fb")
TEST2_PK = bytes.fromhex(
    "3d4017c3e843895a92b70aa74d1b7ebc"
    "9c982ccf2ec4968cc0cd55f12af4660c")
TEST2_MSG = bytes.fromhex("72")
TEST2_SIG = bytes.fromhex(
    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
    "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")


def test_rfc8032_test1_public_key():
    """TEST 1: the derived public key matches the RFC byte-for-byte."""
    assert public_key(TEST1_SK) == TEST1_PK


def test_rfc8032_test1_signature():
    """TEST 1: signing the empty message reproduces the RFC signature."""
    assert sign(TEST1_SK, TEST1_MSG) == TEST1_SIG


def test_rfc8032_test1_verify():
    """TEST 1: the RFC signature verifies; a different message does not."""
    assert verify(TEST1_PK, TEST1_MSG, TEST1_SIG)
    assert not verify(TEST1_PK, b"\x00", TEST1_SIG)


def test_rfc8032_test2_public_key():
    """TEST 2: the derived public key matches the RFC byte-for-byte."""
    assert public_key(TEST2_SK) == TEST2_PK


def test_rfc8032_test2_signature():
    """TEST 2: signing reproduces the RFC signature byte-for-byte."""
    assert sign(TEST2_SK, TEST2_MSG) == TEST2_SIG


def test_rfc8032_test2_verify():
    """TEST 2: the RFC signature verifies; TEST 1's key rejects it."""
    assert verify(TEST2_PK, TEST2_MSG, TEST2_SIG)
    assert not verify(TEST1_PK, TEST2_MSG, TEST2_SIG)
