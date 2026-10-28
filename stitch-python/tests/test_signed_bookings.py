"""
Signed-bookings tests (Track C, ported with the feature from the
verified lab prototype).

Covers the task's required proofs:
  (a) the functional-test impostor scenario (VERIFICATION.md §J) now
      REFUSES under key enforcement — unsigned_booking when the impostor
      books bare, bad_signature when it signs with its own wrong key;
  (b) the same genuine/impostor pair still MINTS with no keys supplied
      (v0.3 semantics preserved exactly);
  (c) a genuine signed pair mints under enforcement (bilateral and
      N-party ring);
  (d) a booking whose ph is altered after signing — with the FNV chain
      honestly recomputed, so chain/law audits pass — refuses with
      bad_signature under enforcement (and payload_mismatch without,
      proving the signature check is what caught it);
  (e) byte-compat: unsigned bookings keep the exact v0.3 descriptor,
      and the whole pre-existing suite runs unchanged (150 passed
      before and after the patch, in the lab and again here).

Fixed test keys (following functional_scenario.py's precedent): the
values are published here on purpose; they prove the mechanism.
"""
import json
from pathlib import Path

import pytest

import stitch
from stitch import (
    Tenant, Ledger,
    book_send, book_receive,
    audit_ledger, agree, agreed_state, agree_n,
    payload_hash,
    BAD_SIGNATURE, UNSIGNED_BOOKING,
)
from stitch.agreement import EXCHANGE_KIND
from stitch.crypto import public_key as ed_public_key, Key
from stitch.stitches import bind

# proart: a string identity with a key (out-of-band binding, as today).
PROART_SK = bytes([7] * 32)
PROART_PK = ed_public_key(PROART_SK)
# thor: identity IS the public-key hex (the recommended form).
THOR_SK = bytes([11] * 32)
THOR_PK = ed_public_key(THOR_SK)
THOR_ID = THOR_PK.hex()
# the impostor's own key — a real key, just not thor's.
IMPOSTOR_SK = bytes([13] * 32)

P_AGREE = {"doc": "harbor-chart", "rev": 3, "place": "Sitka — 世界"}
P_RING = {"doc": "ring-chart", "rev": 1}

KEYS = {"proart": PROART_PK, THOR_ID: THOR_PK}


def _ledger(tmp_path, name):
    path = tmp_path / f"{name}.jsonl"
    return path, Ledger(path=str(path))


def _genuine_pair(tmp_path):
    """proart (signed SEND) <-> thor-as-pubkey-hex (signed RECEIVE)."""
    pa, la = _ledger(tmp_path, "proart")
    proart = Tenant(identity="proart")
    book_send(proart, la, xchg="x-agree", peer=THOR_ID, payload=P_AGREE,
              secret_key=PROART_SK)
    la.close()
    pt, lt = _ledger(tmp_path, "thor")
    thor = Tenant(identity=THOR_ID)
    book_receive(thor, lt, xchg="x-agree", peer="proart", payload=P_AGREE,
                 secret_key=THOR_SK)
    lt.close()
    return pa, pt


def _impostor(tmp_path, sign_with=None):
    """A fresh ledger merely CLAIMING thor's identity, mirroring x-agree.

    This is VERIFICATION.md §J's impostor, verbatim in shape: a Tenant
    with thor's identity string and a book_receive for the same payload.
    """
    pi, li = _ledger(tmp_path, "impostor")
    imp = Tenant(identity=THOR_ID)
    book_receive(imp, li, xchg="x-agree", peer="proart", payload=P_AGREE,
                 secret_key=sign_with)
    li.close()
    return pi


# ----------------------------------------------------------------------
# (c) genuine signed pair mints under enforcement
# ----------------------------------------------------------------------

def test_genuine_signed_pair_mints_under_enforcement(tmp_path):
    pa, pt = _genuine_pair(tmp_path)
    v = agree(str(pa), str(pt), "x-agree", booking_keys=KEYS)
    assert v.agreed and v.reason == "agreed"
    assert v.payload_hash == payload_hash(P_AGREE)
    assert v.sender == "proart" and v.receiver == THOR_ID


def test_genuine_signed_pair_mints_without_enforcement_too(tmp_path):
    # Signed entries must not break the unenforced path.
    pa, pt = _genuine_pair(tmp_path)
    v = agree(str(pa), str(pt), "x-agree")
    assert v.agreed and v.reason == "agreed"


def test_hex_string_keys_accepted(tmp_path):
    pa, pt = _genuine_pair(tmp_path)
    v = agree(str(pa), str(pt), "x-agree",
              booking_keys={"proart": PROART_PK.hex(), THOR_ID: THOR_PK.hex()})
    assert v.agreed


def test_signed_ring_mints_under_enforcement_n_party(tmp_path):
    # 3-party ring, every booking signed, identities are pubkey hexes.
    keys, ids, sks = {}, [], []
    for seed in (21, 22, 23):
        sk = bytes([seed] * 32)
        pk = ed_public_key(sk)
        sks.append(sk)
        ids.append(pk.hex())
        keys[pk.hex()] = pk
    paths = []
    for i in range(3):
        p, led = _ledger(tmp_path, f"ring{i}")
        me = Tenant(identity=ids[i])
        book_send(me, led, xchg="ring-1", peer=ids[(i + 1) % 3],
                  payload=P_RING, secret_key=sks[i])
        book_receive(me, led, xchg="ring-1", peer=ids[(i - 1) % 3],
                     payload=P_RING, secret_key=sks[i])
        led.close()
        paths.append(str(p))
    vn = agree_n(paths, "ring-1", booking_keys=keys)
    assert vn.agreed and vn.reason == "agreed"
    assert sorted(vn.parties) == sorted(ids)


# ----------------------------------------------------------------------
# (a) the impostor now refuses under enforcement
# ----------------------------------------------------------------------

def test_impostor_unsigned_refuses_unsigned_booking(tmp_path):
    pa, _ = _genuine_pair(tmp_path)
    pi = _impostor(tmp_path)  # no key at all — §J's exact shape
    v = agree(str(pa), str(pi), "x-agree", booking_keys=KEYS)
    assert not v.agreed and v.reason == UNSIGNED_BOOKING


def test_impostor_self_signed_refuses_bad_signature(tmp_path):
    pa, _ = _genuine_pair(tmp_path)
    pi = _impostor(tmp_path, sign_with=IMPOSTOR_SK)  # signs — wrong key
    v = agree(str(pa), str(pi), "x-agree", booking_keys=KEYS)
    assert not v.agreed and v.reason == BAD_SIGNATURE


def test_impostor_refused_in_agreed_state_fold(tmp_path):
    pa, _ = _genuine_pair(tmp_path)
    pi = _impostor(tmp_path)
    minted, refused = agreed_state(str(pa), str(pi), booking_keys=KEYS)
    assert minted == []
    assert [(a.xchg, a.reason) for a in refused] == [
        ("x-agree", UNSIGNED_BOOKING)]


def test_wrong_auditor_key_refuses_genuine_pair(tmp_path):
    # Binding is the auditor's claim: keying thor to the WRONG public
    # key makes even the genuine pair refuse — enforcement is only as
    # good as the out-of-band binding, by design and by documentation.
    pa, pt = _genuine_pair(tmp_path)
    wrong = dict(KEYS)
    wrong[THOR_ID] = ed_public_key(IMPOSTOR_SK)
    v = agree(str(pa), str(pt), "x-agree", booking_keys=wrong)
    assert not v.agreed and v.reason == BAD_SIGNATURE


def test_partial_enforcement_only_keyed_party_checked(tmp_path):
    # Keying only proart: thor's booking is unchecked (documented
    # per-party semantics), so the impostor still mints. This test pins
    # the limitation so it can never become an accidental overclaim.
    pa, _ = _genuine_pair(tmp_path)
    pi = _impostor(tmp_path)
    v = agree(str(pa), str(pi), "x-agree",
              booking_keys={"proart": PROART_PK})
    assert v.agreed


# ----------------------------------------------------------------------
# (b) no keys anywhere = v0.3 semantics, limitation intact
# ----------------------------------------------------------------------

def test_impostor_still_mints_with_no_keys_supplied(tmp_path):
    pa, _ = _genuine_pair(tmp_path)
    pi = _impostor(tmp_path)
    v = agree(str(pa), str(pi), "x-agree")  # no booking_keys
    assert v.agreed and v.reason == "agreed"  # §J, unchanged


# ----------------------------------------------------------------------
# (d) ph altered after signing — chain honestly recomputed — refuses
# ----------------------------------------------------------------------

def _rewrite_ph_and_rechain(src: Path, dst: Path):
    """Alter the booking's ph in a copy of a signed ledger and recompute
    the FNV-1a chain honestly, so the copy passes chain + law audit.
    FNV is a canary, not a MAC — this is the attacker the signature
    exists for: one who can rewrite the file AND fix the chain."""
    out_lines = []
    last = stitch.FNV_OFFSET
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        e.pop("h", None)
        if e.get("op") == "BIND" and e.get("kind") == EXCHANGE_KIND:
            ph = e["payload"]["ph"]
            e["payload"]["ph"] = ("0" if ph[0] != "0" else "1") + ph[1:]
        content = json.dumps(e, separators=(",", ":"), sort_keys=True,
                             ensure_ascii=False)
        h = Ledger._fnv1a(content, last)
        last = h
        e["h"] = f"{h:016x}"
        out_lines.append(json.dumps(e, separators=(",", ":"),
                                    sort_keys=True, ensure_ascii=False))
    dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def test_ph_altered_after_signing_refuses_bad_signature(tmp_path):
    pa, pt = _genuine_pair(tmp_path)
    forged = tmp_path / "proart-ph-forged.jsonl"
    _rewrite_ph_and_rechain(pa, forged)
    # The forgery is a perfectly valid ledger by v0.3's checks...
    va = audit_ledger(str(forged))
    assert va.chain_ok and va.law_ok
    # ...without keys it degrades to the ordinary payload_mismatch...
    v0 = agree(str(forged), str(pt), "x-agree")
    assert not v0.agreed and v0.reason == "payload_mismatch"
    # ...and under enforcement the signature is what catches it.
    v = agree(str(forged), str(pt), "x-agree", booking_keys=KEYS)
    assert not v.agreed and v.reason == BAD_SIGNATURE


def test_sig_over_different_ph_refuses(tmp_path):
    # White-box cousin of the above: a booking whose descriptor carries
    # ph_B but a signature computed over ph_A (altered post-signing,
    # embedded in an honestly-written chain).
    pa, la = _ledger(tmp_path, "a")
    a = Tenant(identity="proart")
    ph_b = payload_hash({"doc": "other"})
    desc = {"xchg": "x-tamper", "side": "send", "peer": THOR_ID, "ph": ph_b}
    material = stitch.booking_signing_material(
        "proart", {**desc, "ph": payload_hash(P_AGREE)})
    from stitch.crypto import sign as ed_sign
    desc["sig"] = ed_sign(PROART_SK, material).hex()
    bind(a, la, kind=EXCHANGE_KIND, payload=desc)
    la.close()
    pt, lt = _ledger(tmp_path, "b")
    thor = Tenant(identity=THOR_ID)
    book_receive(thor, lt, xchg="x-tamper", peer="proart",
                 payload={"doc": "other"}, secret_key=THOR_SK)
    lt.close()
    v = agree(str(pa), str(pt), "x-tamper", booking_keys=KEYS)
    assert not v.agreed and v.reason == BAD_SIGNATURE


# ----------------------------------------------------------------------
# (e) byte-compat surface
# ----------------------------------------------------------------------

def test_unsigned_booking_descriptor_is_exactly_v03(tmp_path):
    p, led = _ledger(tmp_path, "plain")
    t = Tenant(identity="plain")
    cell = book_send(t, led, xchg="x1", peer="other", payload={"a": 1})
    led.close()
    assert set(cell.payload.keys()) == {"xchg", "side", "peer", "ph"}
    assert cell.payload["ph"] == payload_hash({"a": 1})


def test_signed_booking_descriptor_adds_only_sig(tmp_path):
    p, led = _ledger(tmp_path, "signed")
    t = Tenant(identity=THOR_ID)
    cell = book_send(t, led, xchg="x1", peer="other", payload={"a": 1},
                     secret_key=THOR_SK)
    led.close()
    assert set(cell.payload.keys()) == {"xchg", "side", "peer", "ph", "sig"}
    assert len(cell.payload["sig"]) == 128


def test_bad_secret_key_length_rejected_at_write(tmp_path):
    p, led = _ledger(tmp_path, "badkey")
    t = Tenant(identity="x")
    with pytest.raises(ValueError):
        book_send(t, led, xchg="x1", peer="y", payload={}, secret_key=b"short")
    led.close()


def test_key_class_round_trip_identity_form(tmp_path):
    # The documented recommended usage: Key.identity as tenant identity.
    key = Key.from_secret(THOR_SK)
    assert key.identity == THOR_ID
    pa, la = _ledger(tmp_path, "k1")
    me = Tenant(identity=key.identity)
    book_send(me, la, xchg="xk", peer="proart", payload=P_AGREE,
              secret_key=key.secret_bytes)
    la.close()
    pt, lt = _ledger(tmp_path, "k2")
    proart = Tenant(identity="proart")
    book_receive(proart, lt, xchg="xk", peer=key.identity, payload=P_AGREE,
                 secret_key=PROART_SK)
    lt.close()
    v = agree(str(pa), str(pt), "xk", booking_keys=KEYS)
    assert v.agreed
