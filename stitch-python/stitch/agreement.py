"""
stitch.agreement — audit, agreement, and minting between sovereign ledgers.

v0.1 could record and verify; it could not agree. This module is the
agreement half, and it is deliberately as small as the rest of stitch:

  - No transport. Ledgers meet as files, whenever they meet.
  - No coordinator, no server, no shared clock. Timestamps are never read.
  - No new stitch. An exchange is booked with the existing BIND stitch.

How an exchange works (Casey's double-entry formulation):

  1. Each side books its half of the exchange into its OWN ledger with
     `book_send` / `book_receive`. A booking is a BIND of kind "exchange"
     whose payload is the descriptor
     ``{"xchg": <id>, "side": "send"|"receive", "peer": <counterpart>,
     "ph": <payload hash>}`` where `ph` is the SHA-256 of the canonical
     JSON of the exchanged payload. The payload itself does not travel in
     the ledger; its hash is the commitment both sides must match.

  2. When the two ledger files meet, `agree` audits BOTH ledgers (hash
     chain + conservation-law replay) and then looks for the mirror pair:
     one side's SEND booked against the other, met by the other's RECEIVE
     booked against the first, same exchange id, same payload hash.

  3. Only then is the exchange shared state — "minted". The Agreement
     returned is a derived value computed from the two files; nothing is
     written anywhere, and either side can re-derive it at any time.
     Every failure is a distinct, explicit refusal — never a silent
     partial fold.

  4. The Agreement pins both ledgers' head hashes and entry counts at
     agreement time. `verify_anchor` later re-checks a ledger against
     that pin: appends since then are fine (the pin is a prefix), but
     tail-truncation or a rewrite of the pinned prefix is caught.

Honest limits (also in SPEC.md):

  - Agreement proves the two RECORDS agree. It does not by itself prove
    a copy of someone's ledger is authentic — tenant identity is a
    string, and keys (where used) are bound to identities out of band.
    v0.3 adds an opt-in signed path: `attest` can carry a real Ed25519
    signature when the writer passes a secret key, and this audit
    verifies such signatures when the caller supplies the tenants'
    public keys (`attest_keys`). Entries written without a key keep the
    v0.1 placeholder tag and are counted as unsigned, not verified.
    Signed bookings (Track C, ported from the verified lab prototype):
    bookings can also be signed, opt-in on both sides —
    `book_send`/`book_receive` accept `secret_key=`, and
    `agree`/`agreed_state`/`agree_n`/`agreed_state_n` accept
    `booking_keys=`. With no keys anywhere, behaviour is unchanged.
  - The audit uses each tenant's in-ledger budget declaration when one
    is in force (declarations are position-aware: a declaration covers
    entries from its own position onward, never earlier ones) and
    otherwise assumes the `budget` parameter (default log2(3)). The
    assumption is carried in every verdict.
  - FNV-1a is a tamper-evident canary, not a cryptographic hash. The
    anchor catches truncation/rewrites relative to a pin you kept; it
    is not a defence against an attacker who can also replace your pin.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Optional, Union

from .core import LOG2_3, FNV_OFFSET, Cell, Ledger, Tenant
from .stitches import bind, TENANT_KIND

PathLike = Union[str, "os.PathLike[str]"]

_EPS = 1e-9

#: The BIND kind used for exchange bookings.
EXCHANGE_KIND = "exchange"

# Refusal / success reason codes returned by `agree`. These strings are
# part of the API: callers may switch on them.
AGREED = "agreed"
NO_SEND = "no_send"
MISSING_MIRROR = "missing_mirror"
PAYLOAD_MISMATCH = "payload_mismatch"
PEER_MISMATCH = "peer_mismatch"
CHAIN_BROKEN = "chain_broken"
LAW_VIOLATED = "law_violated"
# TRACK-C PROTOTYPE (signed bookings) — new refusal reasons. A booking by
# a party the auditor keyed (see `booking_keys` on agree/agree_n) that
# carries no well-formed signature refuses as UNSIGNED_BOOKING; one whose
# signature does not verify refuses as BAD_SIGNATURE. The split mirrors
# the attest audit's verified / unsigned / failures trichotomy: "the
# writer never opted in" is a different operational fact from "this
# signature is wrong", and an auditor responding to them differs.
BAD_SIGNATURE = "bad_signature"
UNSIGNED_BOOKING = "unsigned_booking"


# ============================================================================
# Payload commitment + booking helpers (the write side)
# ============================================================================

def payload_hash(payload) -> str:
    """
    SHA-256 (hex) of the canonical JSON of an exchanged payload.

    Canonical form is the same one the ledger itself uses: sorted keys,
    `(",", ":")` separators. Both sides hash the payload they believe was
    exchanged; the hashes meeting is what `agree` checks.
    """
    # ensure_ascii=False: non-ASCII payload content is hashed over its
    # raw UTF-8 bytes, not the \uXXXX-escaped form. This is the v0.3 pin
    # (see SPEC.md "Byte-exactness"). Both sides of an exchange must hash
    # the same bytes for agree() to mint.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject_non_finite(node, path: str = "payload") -> None:
    """
    Writer-side enforcement of the SPEC numeric policy: non-finite
    floats (NaN, +Infinity, -Infinity) MUST NOT be booked — they are
    invalid JSON tokens, and no other implementation can reproduce
    their hash (a Rust writer cannot construct them at all).

    Booking helpers call this BEFORE any signing or ledger write, so a
    refusal is a pure ValueError: nothing is appended and nothing is
    mutated — the append-only refusal invariant.

    The read side deliberately never calls this: `payload_hash`,
    `agree` and the audit paths hash foreign payloads exactly as they
    find them and return verdicts. Writers are policed; readers are
    not — legacy or foreign ledgers containing non-finite values must
    still audit to a verdict, never crash the auditor.
    """
    if isinstance(node, float):
        if not math.isfinite(node):
            raise ValueError(
                f"non-finite float {node!r} at {path}: NaN/Infinity "
                f"MUST NOT be booked (SPEC numeric policy)")
    elif isinstance(node, dict):
        for k, v in node.items():
            _reject_non_finite(k, f"{path}.<key>")
            _reject_non_finite(v, f"{path}[{k!r}]")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _reject_non_finite(v, f"{path}[{i}]")


def booking_signing_material(ns: str, descriptor: dict) -> bytes:
    """
    TRACK-C PROTOTYPE: the canonical bytes a booking signature covers.

    A versioned JSON object — sorted keys, `(",", ":")` separators, raw
    UTF-8 (the v0.3 pin), exactly the canonicalisation the rest of
    stitch uses — over precisely the booking's commitment fields:
    who booked it (`tenant` = the entry's `ns`), which exchange, which
    side, who the counterpart is, and the payload hash. The `"v"` tag
    is domain separation: a booking signature can never be mistaken
    for an ATTEST signature (whose material is the colon string
    "tenant:cell:claim"), and a future format change gets a new tag
    instead of silently reinterpreting old signatures.

    Deliberately NOT covered: the cell id (it is computed FROM the
    payload, which contains the signature — including it would be
    circular), the entry timestamp, and the ledger's identity as a
    file. Consequence, stated plainly: a genuine signed booking can be
    replayed verbatim into another ledger that claims the same `ns`.
    What signing prevents is FORGERY — booking an exchange the key
    holder never booked, or altering any covered field of a booking
    they did. See REPORT.md / SPEC-DELTA.md for the full discussion.
    """
    material = {
        "v": "stitch-booking/1",
        "tenant": ns,
        "xchg": descriptor.get("xchg"),
        "side": descriptor.get("side"),
        "peer": descriptor.get("peer"),
        "ph": descriptor.get("ph"),
    }
    canonical = json.dumps(material, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return canonical.encode("utf-8")


def _book(tenant: Tenant, ledger: Ledger, xchg: str, peer: str,
          payload, side: str, secret_key=None) -> Cell:
    descriptor = {
        "xchg": xchg,
        "side": side,
        "peer": peer,
        "ph": payload_hash(payload),
    }
    if secret_key is not None:
        # TRACK-C PROTOTYPE: opt-in signed booking. The signature lives
        # inside the booking descriptor (payload key "sig", 128 hex
        # chars) — a booking stays a plain BIND of kind "exchange", no
        # new stitch, and `bind` itself is untouched. With no key the
        # descriptor is exactly the v0.2/v0.3 one, byte for byte.
        if len(secret_key) != 32:
            raise ValueError(
                f"secret_key must be a 32-byte Ed25519 secret key, "
                f"got {len(secret_key)} bytes")
        from .crypto.ed25519 import sign as _ed25519_sign
        material = booking_signing_material(tenant.identity, descriptor)
        descriptor["sig"] = _ed25519_sign(secret_key, material).hex()
    return bind(tenant, ledger, kind=EXCHANGE_KIND, payload=descriptor)


def book_send(tenant: Tenant, ledger: Ledger, xchg: str, peer: str,
              payload, secret_key: Optional[bytes] = None) -> Cell:
    """
    Book the SEND half of exchange `xchg` into your own ledger.

    `peer` is the counterpart's tenant identity; `payload` is the thing
    being exchanged (or a descriptor of it). Only its hash is recorded.
    Costs no γ, like LINK — a booking is an observation, not a surprise.

    TRACK-C PROTOTYPE: pass `secret_key` (32-byte Ed25519 secret key)
    to sign the booking; the descriptor then carries a "sig" field (see
    `booking_signing_material`). An auditor who supplies your public
    key via `agree(..., booking_keys=...)` will REQUIRE that signature
    to verify. Without a key the booking is exactly as in v0.2/v0.3.
    Recommended: use your key's public hex as your tenant identity
    (`Key.identity`), so the ledger's `ns` itself names the key.

    Raises ValueError — before anything is written — if `payload`
    contains a non-finite float anywhere (SPEC numeric policy).
    """
    _reject_non_finite(payload)
    return _book(tenant, ledger, xchg, peer, payload, "send",
                 secret_key=secret_key)


def book_receive(tenant: Tenant, ledger: Ledger, xchg: str, peer: str,
                 payload, secret_key: Optional[bytes] = None) -> Cell:
    """
    Book the RECEIVE half of exchange `xchg` into your own ledger.

    `peer` is the sender's tenant identity; `payload` is what you
    actually received. If it differs from what the sender booked, the
    payload hashes will not meet and `agree` will refuse with
    PAYLOAD_MISMATCH — which is the point.

    `secret_key`: same opt-in signed-booking behaviour as `book_send`.

    Raises ValueError — before anything is written — if `payload`
    contains a non-finite float anywhere (SPEC numeric policy).
    """
    _reject_non_finite(payload)
    return _book(tenant, ledger, xchg, peer, payload, "receive",
                 secret_key=secret_key)


# ============================================================================
# Foreign-ledger audit
# ============================================================================

@dataclass
class AuditVerdict:
    """
    The result of auditing one ledger file you did not write.

    `entries` and the law replay cover the *valid prefix*: if the chain
    breaks at entry k, entries 0..k-1 are what was verified, and
    `chain_broken_at` is k. `head_hash` is the hash of the last entry of
    the valid prefix (None if no entry verified). `tenant` is the single
    tenant identity found in the valid prefix, or None if there were
    several or none.

    `budget` records the fallback budget the law replay used for tenants
    without an in-ledger declaration (default log₂3). `budgets` is the
    per-tenant map of declared budgets (empty if no declarations found);
    a declaration is in force only from its own position onward (see
    `_audit`), so `budgets` is the set of claims, not proof that a given
    budget covered any particular entry.

    The `attest_*` fields are populated only when the audit is given
    public keys (`attest_keys`): `attest_verified` counts ATTEST entries
    whose Ed25519 signature verified, `attest_unsigned` counts ATTEST
    entries by keyed tenants that carry no Ed25519 signature (e.g. the
    v0.1 placeholder), and `attest_failures` lists signatures that did
    not verify. Without keys all three stay at their defaults and `ok`
    is exactly the v0.2 meaning.
    """
    path: str
    tenant: Optional[str]
    entries: int
    chain_ok: bool
    chain_broken_at: Optional[int]
    law_ok: bool
    law_excess: float
    law_violations: list = field(default_factory=list)
    head_hash: Optional[str] = None
    budget: float = LOG2_3
    budgets: dict = field(default_factory=dict)
    attest_verified: int = 0
    attest_unsigned: int = 0
    attest_failures: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when both the chain and the conservation law verify."""
        return self.chain_ok and self.law_ok

    @property
    def signatures_ok(self) -> bool:
        """
        True when no supplied-key ATTEST signature failed verification.

        Vacuously True when the audit was not given keys — check
        `attest_verified` if you need to know any were actually checked.
        """
        return not self.attest_failures


def _normalise_pubkey(identity: str, key) -> bytes:
    """An attest_keys value as 32 raw bytes (bytes or 64-char hex str)."""
    if isinstance(key, str):
        try:
            key = bytes.fromhex(key)
        except ValueError:
            raise ValueError(
                f"attest_keys[{identity!r}] is not valid hex") from None
    if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
        raise ValueError(
            f"attest_keys[{identity!r}] must be a 32-byte Ed25519 public "
            f"key (bytes or 64-char hex), got {key!r}")
    return bytes(key)


def _audit(path: PathLike, budget: float, attest_keys=None):
    """
    Audit `path`; return (AuditVerdict, [valid entries as dicts]).

    `attest_keys`: optional {tenant identity: Ed25519 public key} map.
    When given, ATTEST entries by those tenants are signature-checked
    (see AuditVerdict). When None (the default), no signature work is
    done at all and the verdict's attest_* fields stay at defaults.
    """
    if attest_keys is not None:
        from .crypto.ed25519 import verify as _ed25519_verify
        attest_keys = {ns: _normalise_pubkey(ns, k)
                       for ns, k in attest_keys.items()}
    path_str = os.fspath(path)
    raw_lines = []
    with open(path_str, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_lines.append(line)

    entries: list = []
    chain_broken_at: Optional[int] = None
    last = FNV_OFFSET
    head_hash: Optional[str] = None
    for i, line in enumerate(raw_lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            chain_broken_at = i
            break
        h_str = entry.pop("h", None)
        if h_str is None:
            chain_broken_at = i
            break
        content = json.dumps(entry, separators=(",", ":"), sort_keys=True,
                              ensure_ascii=False)
        expected = Ledger._fnv1a(content, last)
        if f"{expected:016x}" != h_str:
            chain_broken_at = i
            break
        entries.append(entry)
        last = int(h_str, 16)
        head_hash = h_str

    # Conservation-law replay over the valid prefix: γ accumulates each
    # EFFECT's recorded s per tenant, resets on TICK, and every entry's
    # recorded g must equal the replayed running sum and stay in budget.
    #
    # v0.3: per-tenant budgets may be declared in-ledger with a BIND of
    # kind="tenant" and payload={"budget": <float>} (see declare_budget).
    # First declaration wins; subsequent declarations are recorded but
    # ignored. Tenants without an in-ledger declaration fall back to the
    # `budget` parameter (default log₂3) — preserves backward
    # compatibility with v0.2 ledgers.
    #
    # Declarations are POSITION-AWARE (patch applied on adoption of
    # v0.3 — the draft extracted budgets in a pre-pass over the whole
    # valid prefix, so a declaration appended after an overspend
    # retroactively excused it): a declaration takes effect at its own
    # position and covers only entries from there on. Surprise spent
    # before a declaration stands is judged against the budget in force
    # at the position where it was spent.
    declared_budgets: dict = {}
    active_budgets: dict = {}

    gamma: dict = {}
    violations: list = []
    excess = 0.0
    attest_verified = 0
    attest_unsigned = 0
    attest_failures: list = []
    for i, e in enumerate(entries):
        ns = e.get("ns", "?")
        # A first budget declaration takes effect at its own position.
        if (e.get("op") == "BIND" and e.get("kind") == TENANT_KIND
                and isinstance(e.get("payload"), dict)
                and ns not in declared_budgets):
            b = e["payload"].get("budget")
            if isinstance(b, (int, float)) and b > 0:
                declared_budgets[ns] = float(b)
                active_budgets[ns] = float(b)
        tenant_budget = active_budgets.get(ns, budget)
        g = gamma.get(ns, 0.0)
        if e.get("op") == "EFFECT":
            g += float(e.get("s", 0.0))
        elif e.get("op") == "TICK":
            g = 0.0
        gamma[ns] = g
        recorded = float(e.get("g", 0.0))
        if abs(recorded - g) > 1e-6:
            violations.append(
                f"entry {i}: recorded g={recorded} != replayed "
                f"gamma={g:.4f} (tenant {ns}, op {e.get('op')})"
            )
        if g > tenant_budget + _EPS:
            excess = max(excess, g - tenant_budget)
            violations.append(
                f"entry {i}: gamma={g:.4f} exceeds budget "
                f"{tenant_budget:.4f} by {g - tenant_budget:.4f} "
                f"(tenant {ns}, op {e.get('op')})"
            )
        # Opt-in ATTEST signature verification: only for tenants whose
        # public key the caller supplied, and only now that the entry is
        # known to sit inside the valid (chain-verified) prefix.
        if (attest_keys is not None and e.get("op") == "ATTEST"
                and ns in attest_keys):
            sig = e.get("sig")
            sig_bytes = None
            if isinstance(sig, str) and len(sig) == 128:
                try:
                    sig_bytes = bytes.fromhex(sig)
                except ValueError:
                    sig_bytes = None
            if sig_bytes is None:
                attest_unsigned += 1
            else:
                material = f"{ns}:{e.get('cell')}:{e.get('claim')}"
                if _ed25519_verify(attest_keys[ns],
                                   material.encode("utf-8"), sig_bytes):
                    attest_verified += 1
                else:
                    attest_failures.append(
                        f"entry {i}: ATTEST signature does not verify "
                        f"for tenant {ns} (cell {e.get('cell')}, "
                        f"claim {e.get('claim')!r})"
                    )

    tenants = {e.get("ns") for e in entries}
    verdict = AuditVerdict(
        path=path_str,
        tenant=tenants.pop() if len(tenants) == 1 else None,
        entries=len(entries),
        chain_ok=chain_broken_at is None,
        chain_broken_at=chain_broken_at,
        law_ok=not violations,
        law_excess=excess,
        law_violations=violations,
        head_hash=head_hash,
        budget=budget,
        budgets=declared_budgets,
        attest_verified=attest_verified,
        attest_unsigned=attest_unsigned,
        attest_failures=attest_failures,
    )
    return verdict, entries


def audit_ledger(path: PathLike, budget: float = LOG2_3,
                 attest_keys=None) -> AuditVerdict:
    """
    Audit a foreign ledger from a local copy: hash chain + conservation
    law, as one structured verdict. Reads only; never opens for append.
    Raises FileNotFoundError if the file does not exist.

    `attest_keys` (optional): {tenant identity: Ed25519 public key as
    bytes or 64-char hex}. When supplied, ATTEST entries written by
    those tenants with a real signature (see `attest`) are verified and
    the verdict reports `attest_verified` / `attest_unsigned` /
    `attest_failures`. When omitted, no signature verification happens.
    """
    verdict, _ = _audit(path, budget, attest_keys)
    return verdict


# ============================================================================
# TRACK-C PROTOTYPE — booking signature enforcement (opt-in, auditor-side)
# ============================================================================

def _normalise_booking_keys(booking_keys) -> dict:
    """A booking_keys map with values as 32 raw public-key bytes."""
    out = {}
    for identity, key in booking_keys.items():
        if isinstance(key, str):
            try:
                key = bytes.fromhex(key)
            except ValueError:
                raise ValueError(
                    f"booking_keys[{identity!r}] is not valid hex") from None
        if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
            raise ValueError(
                f"booking_keys[{identity!r}] must be a 32-byte Ed25519 "
                f"public key (bytes or 64-char hex), got {key!r}")
        out[identity] = bytes(key)
    return out


def _booking_signature_failure(bookings, booking_keys):
    """
    Check every booking in `bookings` ([(ns, descriptor), ...]) whose
    writer the auditor keyed. Return None if all keyed bookings carry
    valid signatures, else (reason, detail) for the first failure.

    Fail-closed scope: EVERY booking for the exchange by a keyed party
    is checked, not just the one that would end up mirroring — an
    auditor should not have to guess which of several bookings is
    "the real one". Bookings by parties with no supplied key are not
    checked at all: enforcement is per-party, exactly as supplied.
    """
    if not booking_keys:
        return None
    from .crypto.ed25519 import verify as _ed25519_verify
    keys = _normalise_booking_keys(booking_keys)
    for ns, p in bookings:
        if ns not in keys:
            continue
        sig = p.get("sig")
        sig_bytes = None
        if isinstance(sig, str) and len(sig) == 128:
            try:
                sig_bytes = bytes.fromhex(sig)
            except ValueError:
                sig_bytes = None
        if sig_bytes is None:
            return (UNSIGNED_BOOKING,
                    f"booking for exchange {p.get('xchg')!r} by {ns} "
                    f"(side={p.get('side')}) carries no Ed25519 "
                    f"signature, but the auditor supplied a key for "
                    f"that party")
        material = booking_signing_material(ns, p)
        if not _ed25519_verify(keys[ns], material, sig_bytes):
            return (BAD_SIGNATURE,
                    f"booking signature does not verify for {ns} "
                    f"(exchange {p.get('xchg')!r}, side={p.get('side')}, "
                    f"peer={p.get('peer')}, ph={p.get('ph')})")
    return None


# ============================================================================
# Bilateral agreement / minting
# ============================================================================

@dataclass
class Agreement:
    """
    The verdict on one exchange between two ledger copies.

    When `agreed` is True this object IS the minted shared state for the
    exchange — a derived value, recomputable from the two files at any
    time — and `head_a`/`head_b` + `entries_a`/`entries_b` pin the two
    ledgers as they were at agreement time (see `verify_anchor`).
    When `agreed` is False, `reason` is one of the module-level reason
    codes and `detail` says exactly what failed. Nothing is ever
    partially minted.
    """
    xchg: str
    agreed: bool
    reason: str
    detail: str
    sender: Optional[str] = None
    receiver: Optional[str] = None
    payload_hash: Optional[str] = None
    head_a: Optional[str] = None
    head_b: Optional[str] = None
    entries_a: int = 0
    entries_b: int = 0


def _bookings(entries: list, xchg: str):
    """Exchange bookings for `xchg` in a parsed entry list: [(ns, descriptor)]."""
    out = []
    for e in entries:
        if e.get("op") == "BIND" and e.get("kind") == EXCHANGE_KIND:
            p = e.get("payload") or {}
            if p.get("xchg") == xchg:
                out.append((e.get("ns"), p))
    return out


def agree(path_a: PathLike, path_b: PathLike, xchg: str,
          budget: float = LOG2_3, booking_keys=None) -> Agreement:
    """
    Decide whether exchange `xchg` is shared state between two ledgers.

    Both ledgers are audited first; a broken chain or a law violation on
    either side refuses everything. The exchange mints only if one side
    booked SEND against the other and the other booked the mirror
    RECEIVE — same exchange id, same payload hash, counterpart identities
    naming each other. Argument order does not matter (the sender is
    whoever booked the SEND), but `head_a`/`entries_a` always describe
    the first argument's ledger.

    TRACK-C PROTOTYPE: `booking_keys` (optional) maps tenant identity
    to an Ed25519 public key (bytes or 64-char hex) — the auditor's
    out-of-band binding of keys to identities. When a key is supplied
    for a party, EVERY booking for `xchg` by that party in either
    ledger must carry a valid signature (see `book_send(secret_key=)`)
    or the verdict refuses: UNSIGNED_BOOKING if it carries no
    well-formed signature, BAD_SIGNATURE if one does not verify.
    Signature enforcement runs after the chain/law audits and before
    mirror matching (fail-closed). When `booking_keys` is None (the
    default), no signature work happens and the semantics are exactly
    v0.3's. Supplying keys for only one party enforces only that party.
    """
    va, entries_a = _audit(path_a, budget)
    vb, entries_b = _audit(path_b, budget)
    base = dict(head_a=va.head_hash, head_b=vb.head_hash,
                entries_a=va.entries, entries_b=vb.entries)

    def refuse(reason: str, detail: str) -> Agreement:
        return Agreement(xchg=xchg, agreed=False, reason=reason,
                         detail=detail, **base)

    if not va.chain_ok:
        return refuse(CHAIN_BROKEN,
                      f"{va.path}: hash chain broken at entry {va.chain_broken_at}")
    if not vb.chain_ok:
        return refuse(CHAIN_BROKEN,
                      f"{vb.path}: hash chain broken at entry {vb.chain_broken_at}")
    if not va.law_ok:
        return refuse(LAW_VIOLATED,
                      f"{va.path}: {va.law_violations[0]}")
    if not vb.law_ok:
        return refuse(LAW_VIOLATED,
                      f"{vb.path}: {vb.law_violations[0]}")

    # TRACK-C: opt-in booking signature enforcement (no-op when the
    # auditor supplied no keys — the v0.3 path, unchanged).
    sig_failure = _booking_signature_failure(
        _bookings(entries_a, xchg) + _bookings(entries_b, xchg),
        booking_keys)
    if sig_failure is not None:
        return refuse(sig_failure[0], sig_failure[1])

    # Candidates: (sender_ns, descriptor, counterpart_entries), ledger A
    # scanned before ledger B so the outcome is deterministic.
    sends = []
    for ns, p in _bookings(entries_a, xchg):
        if p.get("side") == "send":
            sends.append((ns, p, entries_b))
    for ns, p in _bookings(entries_b, xchg):
        if p.get("side") == "send":
            sends.append((ns, p, entries_a))
    if not sends:
        return refuse(NO_SEND,
                      f"no SEND booking for exchange {xchg!r} in either ledger")

    first_failure: Optional[Agreement] = None
    for sender, p, other_entries in sends:
        peer = p.get("peer")
        receives = [(ns, q) for ns, q in _bookings(other_entries, xchg)
                    if q.get("side") == "receive"]
        if not receives:
            failure = refuse(
                MISSING_MIRROR,
                f"{sender} booked SEND of {xchg!r} to {peer}, but the "
                f"counterpart ledger has no RECEIVE booking for it")
        else:
            failure = None
            for r_ns, q in receives:
                if r_ns != peer or q.get("peer") != sender:
                    failure = refuse(
                        PEER_MISMATCH,
                        f"RECEIVE booking for {xchg!r} is between {r_ns} "
                        f"and {q.get('peer')}, not {sender} and {peer}")
                elif q.get("ph") != p.get("ph"):
                    failure = refuse(
                        PAYLOAD_MISMATCH,
                        f"payload hash mismatch for {xchg!r}: sender "
                        f"booked ph={p.get('ph')}, receiver booked "
                        f"ph={q.get('ph')}")
                else:
                    return Agreement(
                        xchg=xchg, agreed=True, reason=AGREED,
                        detail=f"{sender} -> {peer}: mirror bookings agree "
                               f"on payload hash {p.get('ph')}",
                        sender=sender, receiver=peer,
                        payload_hash=p.get("ph"), **base)
        if first_failure is None:
            first_failure = failure
    assert first_failure is not None  # sends was non-empty
    return first_failure


def agreed_state(path_a: PathLike, path_b: PathLike,
                 budget: float = LOG2_3, booking_keys=None):
    """
    The derived fold of two ledgers: (agreed, refused) — an Agreement for
    every exchange id booked in either ledger, partitioned by outcome,
    each list sorted by exchange id. The agreed list is the pair's
    shared state; it exists only here, as a fold of the two records.

    `booking_keys` is passed through to `agree` for every exchange (see
    its docstring); the default None preserves v0.3 semantics exactly.
    """
    _, entries_a = _audit(path_a, budget)
    _, entries_b = _audit(path_b, budget)
    ids = set()
    for entries in (entries_a, entries_b):
        for e in entries:
            if e.get("op") == "BIND" and e.get("kind") == EXCHANGE_KIND:
                ids.add((e.get("payload") or {}).get("xchg"))
    agreed, refused = [], []
    for xchg in sorted(ids):
        verdict = agree(path_a, path_b, xchg, budget=budget,
                        booking_keys=booking_keys)
        (agreed if verdict.agreed else refused).append(verdict)
    return agreed, refused


# ============================================================================
# Head-hash anchor (re-audit against an agreement-time pin)
# ============================================================================

@dataclass
class AnchorVerdict:
    """
    Re-audit of one ledger against a head-hash pin from agreement time.

    status:
      "anchored"     — the pinned head is intact at the pinned position;
                       the ledger is unchanged there or has only grown.
      "truncated"    — fewer entries than pinned, prefix intact: the
                       tail was cut after the pin was taken.
      "rewritten"    — the entry at the pinned position has a different
                       hash: history at or before the pin was replaced.
      "chain_broken" — the chain fails inside the pinned prefix.
      "missing"      — the file is gone.
    """
    status: str
    path: str
    entries: int
    pinned_entries: int
    head_hash: Optional[str]
    pinned_head: Optional[str]


def verify_anchor(path: PathLike, head_hash: Optional[str],
                  entry_count: int) -> AnchorVerdict:
    """
    Check a ledger against a pin (head hash + entry count) taken earlier,
    typically from an Agreement. Pure file read; no clock, no network.
    """
    path_str = os.fspath(path)
    if not os.path.exists(path_str):
        return AnchorVerdict("missing", path_str, 0, entry_count, None, head_hash)
    verdict, entries = _audit(path_str, LOG2_3)
    current_head = verdict.head_hash
    if entry_count == 0:
        status = "anchored" if head_hash is None else "rewritten"
        return AnchorVerdict(status, path_str, verdict.entries, 0,
                             current_head, head_hash)
    if not verdict.chain_ok and (verdict.chain_broken_at or 0) < entry_count:
        return AnchorVerdict("chain_broken", path_str, verdict.entries,
                             entry_count, current_head, head_hash)
    if verdict.entries < entry_count:
        return AnchorVerdict("truncated", path_str, verdict.entries,
                             entry_count, current_head, head_hash)
    # The chain is intact at least through the pinned prefix (any break
    # is beyond it), so recompute the hash at the pinned position by
    # walking the raw file exactly as the chain does.
    pinned_entry_hash = None
    with open(path_str, "r", encoding="utf-8") as f:
        idx = -1
        for line in f:
            line = line.strip()
            if not line:
                continue
            idx += 1
            if idx == entry_count - 1:
                pinned_entry_hash = json.loads(line).get("h")
                break
    status = "anchored" if pinned_entry_hash == head_hash else "rewritten"
    return AnchorVerdict(status, path_str, verdict.entries, entry_count,
                         current_head, head_hash)


# ============================================================================
# N-party agreement (v0.3 — closes the "bilateral only" gap)
# ============================================================================

@dataclass
class AgreementN:
    """
    The verdict on one exchange across N ledger copies (N >= 2).

    v0.2's `Agreement` is bilateral (two ledgers). v0.3 generalises to N
    parties forming a closed cycle of mirror bookings: every SEND booked
    in any ledger has a corresponding RECEIVE booked by the named peer in
    some other ledger, all bookings agree on the payload hash, and every
    ledger has at least one booking for the exchange.

    For N=2 this reduces exactly to `agree` — a bilateral exchange is a
    2-cycle. For N>2 a typical ring is: A books SEND to B and RECEIVE
    from C; B books SEND to C and RECEIVE from A; C books SEND to A and
    RECEIVE from B. The audit verifies each SEND has its mirror RECEIVE.

    `heads` and `entries_list` are parallel arrays indexed the same as
    the input `paths` — they pin each ledger's head hash and entry count
    at agreement time, for later `verify_anchor` checks.

    When `agreed` is False, `reason` is one of the module-level reason
    codes (NO_SEND, MISSING_MIRROR, PAYLOAD_MISMATCH, PEER_MISMATCH,
    CHAIN_BROKEN, LAW_VIOLATED) and `detail` says exactly what failed.
    """
    xchg: str
    agreed: bool
    reason: str
    detail: str
    parties: list = field(default_factory=list)
    payload_hash: Optional[str] = None
    heads: list = field(default_factory=list)
    entries_list: list = field(default_factory=list)
    paths: list = field(default_factory=list)


def agree_n(paths, xchg: str, budget: float = LOG2_3,
            booking_keys=None) -> AgreementN:
    """
    Decide whether exchange `xchg` is shared state across N ledgers.

    The rule is the v0.2 bilateral rule generalised: every SEND booked
    in any ledger must have a mirror RECEIVE booked by the named peer in
    some OTHER ledger, with the same payload hash and counterpart
    identities naming each other. RECEIVE bookings without a SEND
    counterpart are observations, not commitments — they don't trigger
    a refusal (this matches v0.2 semantics).

    All N ledgers are audited first; any chain break or law violation
    on any side refuses everything. The exchange mints only if:

      1. Every ledger has at least one booking for `xchg`.
      2. All bookings across all ledgers agree on the same payload hash.
      3. Every SEND has a mirror RECEIVE in some other ledger.

    For N=2 this is exactly `agree` — a 2-cycle. For N>2 the typical
    ring pattern is each party booking a SEND to their successor and a
    RECEIVE from their predecessor.

    TRACK-C PROTOTYPE: `booking_keys` enforces booking signatures for
    keyed parties exactly as in `agree` (UNSIGNED_BOOKING /
    BAD_SIGNATURE refusals); default None = v0.3 semantics.
    """
    path_strs = [os.fspath(p) for p in paths]

    # Audit all N ledgers first
    audits: list = []
    for p in path_strs:
        v, entries = _audit(p, budget)
        audits.append((v, entries))

    heads = [v.head_hash for v, _ in audits]
    entries_list = [v.entries for v, _ in audits]
    base = dict(heads=heads, entries_list=entries_list, paths=path_strs)

    def refuse(reason: str, detail: str) -> AgreementN:
        return AgreementN(xchg=xchg, agreed=False, reason=reason,
                           detail=detail, **base)

    # Any chain break or law violation refuses everything
    for i, (v, _) in enumerate(audits):
        if not v.chain_ok:
            return refuse(CHAIN_BROKEN,
                          f"{path_strs[i]}: hash chain broken at entry {v.chain_broken_at}")
        if not v.law_ok:
            return refuse(LAW_VIOLATED,
                          f"{path_strs[i]}: {v.law_violations[0]}")

    # Collect all bookings for xchg across all ledgers
    # Each: (ledger_idx, ns, descriptor)
    all_bookings: list = []
    for i, (_, entries) in enumerate(audits):
        for e in entries:
            if e.get("op") == "BIND" and e.get("kind") == EXCHANGE_KIND:
                p = e.get("payload") or {}
                if p.get("xchg") == xchg:
                    all_bookings.append((i, e.get("ns"), p))

    # TRACK-C: opt-in booking signature enforcement, same rule as
    # bilateral agree() — after chain/law, before mirror semantics,
    # no-op when no keys are supplied. (If this were left out, the
    # N-party path would be a trivial bypass of the bilateral check.)
    sig_failure = _booking_signature_failure(
        [(ns, p) for _, ns, p in all_bookings], booking_keys)
    if sig_failure is not None:
        return refuse(sig_failure[0], sig_failure[1])

    if not all_bookings:
        return refuse(NO_SEND,
                      f"no booking for exchange {xchg!r} in any of the {len(paths)} ledgers")

    # Check every ledger has at least one booking
    ledgers_with_bookings = {b[0] for b in all_bookings}
    if len(ledgers_with_bookings) < len(path_strs):
        missing = [path_strs[i] for i in range(len(path_strs))
                   if i not in ledgers_with_bookings]
        return refuse(MISSING_MIRROR,
                      f"{len(missing)} ledger(s) have no booking for {xchg!r}: {missing}")

    # Check all bookings agree on the same payload hash
    phs = {b[2].get("ph") for b in all_bookings}
    if len(phs) > 1:
        return refuse(PAYLOAD_MISMATCH,
                      f"multiple payload hashes for {xchg!r}: {phs}")
    ph = phs.pop()

    # For every SEND booking, look for a mirror RECEIVE in some OTHER ledger.
    # A SEND by ns to peer with ph needs a RECEIVE by peer from ns with ph,
    # in some ledger other than the SEND's ledger.
    for ledger_idx, ns, p in all_bookings:
        if p.get("side") != "send":
            continue  # RECEIVEs are observations, not commitments — skip
        peer = p.get("peer")
        found_mirror = False
        for other_idx, other_ns, other_p in all_bookings:
            if other_idx == ledger_idx:
                continue  # mirror must be in a different ledger
            if (other_ns == peer
                and other_p.get("side") == "receive"
                    and other_p.get("peer") == ns
                    and other_p.get("ph") == ph):
                found_mirror = True
                break
        if not found_mirror:
            return refuse(MISSING_MIRROR,
                          f"SEND by {ns} to {peer} (ph={ph[:8]}) has no "
                          f"mirror RECEIVE in any other ledger")

    # All checks passed — mint
    parties = sorted({b[1] for b in all_bookings})
    return AgreementN(
        xchg=xchg, agreed=True, reason=AGREED,
        detail=f"{len(parties)} parties agreed on payload hash {ph}",
        parties=parties, payload_hash=ph, **base,
    )


def agreed_state_n(paths, budget: float = LOG2_3, booking_keys=None):
    """
    The derived fold of N ledgers: (agreed, refused) — an AgreementN for
    every exchange id booked in any of the N ledgers, partitioned by
    outcome, each list sorted by exchange id. The agreed list is the
    N-party shared state; it exists only here, as a fold of the records.
    """
    path_strs = [os.fspath(p) for p in paths]

    # Audit all N ledgers once
    all_entries: list = []
    for p in path_strs:
        _, entries = _audit(p, budget)
        all_entries.append(entries)

    # Collect all exchange ids across all ledgers
    ids = set()
    for entries in all_entries:
        for e in entries:
            if e.get("op") == "BIND" and e.get("kind") == EXCHANGE_KIND:
                ids.add((e.get("payload") or {}).get("xchg"))

    agreed, refused = [], []
    for xchg in sorted(ids):
        verdict = agree_n(path_strs, xchg, budget=budget,
                          booking_keys=booking_keys)
        (agreed if verdict.agreed else refused).append(verdict)
    return agreed, refused
