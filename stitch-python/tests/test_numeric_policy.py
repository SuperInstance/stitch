"""
Numeric policy tests — pins the v0.4 numeric byte-exactness policy on
the Python side (SPEC.md "Numeric policy (v0.4)").

Two jobs:
  1. Pin Python's canonical number texts — the cross-language reference
     form — for the portable set measured by the 2026-10-27 fuzz study.
  2. Assert `payload_hash` stability for the study's key divergence
     examples as fixed SHA-256 vectors: a conformant implementation
     hashing the same canonical bytes must reproduce these exactly.

The study's three hazards these tests keep visible: writer-vs-writer
float formatting in the 1e-9..1e-4 band (Python `1e-05`, Rust Ryu
`0.00001`), ±1 ulp parser drift on 14–17-digit floats (serde_json's
default parser; Python's parse is correctly rounded), and lossy
out-of-range integers (2**64). The Rust audit path is made safe against
the latter two by serde_json's arbitrary_precision feature; the band is
a writer policy — writers avoid it in commitments.
"""

import json
import hashlib
import pytest

from stitch import payload_hash


def canonical(x) -> str:
    """The canonical form every hashed line and commitment uses."""
    return json.dumps(x, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


# The portable float set: canonical texts Python (the reference writer)
# emits. Every conformant verifier must hash these exact bytes when they
# appear in a line written by Python. A list of pairs, not a dict: -0.0
# and 0.0 are equal and hash equal in Python, so a dict would collapse
# them.
PORTABLE_FLOAT_TEXTS = [
    (1e-05, "1e-05"),
    (1e-06, "1e-06"),
    (1.5e-07, "1.5e-07"),
    (9.999e-05, "9.999e-05"),
    (-4.7620818645605604e-05, "-4.7620818645605604e-05"),
    (123456789.12345679, "123456789.12345679"),  # the ±1 ulp drift case
    (0.1, "0.1"),
    (1e16, "1e+16"),
    (-0.0, "-0.0"),
    (0.0, "0.0"),
    (5e-324, "5e-324"),  # min subnormal
    (1.7976931348623157e308, "1.7976931348623157e+308"),  # max double
]

# Fixed payload_hash vectors for the study's key examples. The digest is
# SHA-256 over the canonical bytes shown — stable across processes,
# platforms, and library refactors, and the value any conformant
# implementation must derive for the same payload.
PAYLOAD_HASH_VECTORS = {
    '{"v":1e-05}':
        "20c9ecb845127016018197861d71e138deb967639c236c61fc1b244d7234b5f4",
    '{"v":1e-06}':
        "5cbf3885da23aaa863fa0dcb968dbe3c77d1668479ddd28ddaefe3f652b0ff24",
    '{"v":1.5e-07}':
        "5808eb82e119507143e4105fe58550de9ddf00c0decda7c26d848431db965eca",
    '{"v":9.999e-05}':
        "1ae16d218d971a8a7ff73c377641acf13537ba326849060283e09785fb009c5a",
    '{"v":123456789.12345679}':
        "eaa94399e3d1dc92a5eee8a94276edc968571cc31a714d03657624416598b5c6",
    '{"v":-4.7620818645605604e-05}':
        "3c23f28e6f4ee16c2ee4297b7d8492b6a2d1c81c7705bccdbed892719aee9d3a",
    # 2**64 as a JSON number — the hazard form a Python writer can emit
    # today (exact in Python, lossy under a non-conformant parser).
    '{"big":18446744073709551616}':
        "14610a8bbe2a1059a67255f85a8562472b95b15e87bdf77dab7f3d5d5690af4c",
    # 2**64 as a decimal string — the policy-mandated form.
    '{"big":"18446744073709551616"}':
        "80490abab72a31c104c6d7cf7633243f5777688b075ad6bb5f8fd753a8c87c0f",
    '{"readings":[0.1,1e+16,5e-324]}':
        "4a48fb8e98a49aea8a544370f47fc96efdc403c075fc3ef132de1074a2f1467b",
}


def test_canonical_float_texts_portable_set():
    """Python's canonical float texts are pinned, band and boundaries."""
    for value, text in PORTABLE_FLOAT_TEXTS:
        assert canonical(value) == text


def test_parse_reserialize_is_stable_for_key_examples():
    """Python's parse -> canonical re-serialize is the identity on these
    texts (correctly-rounded parse + shortest round-trip repr). This is
    the property serde_json's default parser lacks: it parses
    123456789.12345679 one ulp away (123456789.1234568) and re-formats
    the band (1e-05 -> 0.00001) — why the Rust audit path preserves
    number text verbatim instead."""
    for _, text in PORTABLE_FLOAT_TEXTS:
        assert canonical(json.loads(text)) == text


def test_payload_hash_stability_vectors():
    """payload_hash is stable and matches the SPEC formula, byte for
    byte, for the fuzz study's key examples."""
    for canonical_text, expected in PAYLOAD_HASH_VECTORS.items():
        payload = json.loads(canonical_text)
        # Formula pin: SHA-256 over the canonical bytes...
        assert payload_hash(payload) == \
            hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
        # ...and the fixed vector (stability across refactors).
        assert payload_hash(payload) == expected
        # Deterministic across calls.
        assert payload_hash(payload) == payload_hash(payload)


def test_payload_hash_key_order_independent():
    """Canonicalization sorts keys, so insertion order cannot move a
    commitment."""
    a = {"v": 1e-05, "x": 1}
    b = {"x": 1, "v": 1e-05}
    assert payload_hash(a) == payload_hash(b)


def test_integer_boundaries_portable_as_numbers():
    """Integers inside [-2^63, 2^64-1] are portable JSON numbers: exact
    canonical digits at every boundary. 2^64 itself is outside the
    portable set — the policy form is a decimal string."""
    inside = {
        0: "0",
        1: "1",
        -1: "-1",
        2**53: "9007199254740992",
        2**63 - 1: "9223372036854775807",
        -2**63: "-9223372036854775808",
        2**64 - 1: "18446744073709551615",
    }
    for value, text in inside.items():
        assert canonical(value) == text
        assert canonical(json.loads(text)) == text
    # Outside the portable set: full digits are emitted (exact in
    # Python), but other implementations degrade them lossily — the
    # policy says carry as a string.
    assert canonical(2**64) == "18446744073709551616"
    assert canonical({"big": str(2**64)}) == '{"big":"18446744073709551616"}'


def test_big_integer_forms_are_distinct_commitments():
    """The number form and the policy string form of a big integer are
    different commitments. A writer must pick the policy form; a reader
    must not conflate them."""
    as_number = payload_hash({"big": 2**64})
    as_string = payload_hash({"big": str(2**64)})
    assert as_number != as_string


def test_band_float_forms_are_distinct_commitments():
    """Same double, three texts: the Python canonical number form and
    the string forms hash differently. This is the writer-vs-writer
    hazard the policy's band-avoidance rule exists for — an auditor
    cannot repair it after the fact, only writers can avoid it."""
    texts = ["1e-05", "0.00001"]
    digests = {payload_hash({"v": json.loads(t)}): t for t in texts}
    assert len(digests) == 1  # the number form is one commitment...
    assert payload_hash({"v": "1e-05"}) not in digests  # ...strings differ
    assert payload_hash({"v": "0.00001"}) not in digests
    assert payload_hash({"v": "1e-05"}) != payload_hash({"v": "0.00001"})


def test_non_finite_floats_not_bookable_per_policy():
    """Non-finite floats MUST NOT be booked (invalid JSON). The stdlib
    rejection semantics the policy's `allow_nan=False` rule names, at
    every position a payload can carry one."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            json.dumps(bad, allow_nan=False)
        with pytest.raises(ValueError):
            json.dumps({"v": bad}, allow_nan=False)
        with pytest.raises(ValueError):
            json.dumps([0, bad], allow_nan=False)
