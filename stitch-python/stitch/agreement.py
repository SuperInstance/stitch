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

  - Agreement proves the two RECORDS agree. It does not prove a copy of
    someone's ledger is authentic — identity in v0.2 is still a string,
    and signatures are future work. Hold copies you trust.
  - The audit assumes a per-tenant budget (default log2(3)), because
    ledgers do not record configured budgets. The assumption is carried
    in every verdict.
  - FNV-1a is a tamper-evident canary, not a cryptographic hash. The
    anchor catches truncation/rewrites relative to a pin you kept; it
    is not a defence against an attacker who can also replace your pin.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Optional, Union

from .core import LOG2_3, FNV_OFFSET, Cell, Ledger, Tenant
from .stitches import bind

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
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _book(tenant: Tenant, ledger: Ledger, xchg: str, peer: str,
          payload, side: str) -> Cell:
    descriptor = {
        "xchg": xchg,
        "side": side,
        "peer": peer,
        "ph": payload_hash(payload),
    }
    return bind(tenant, ledger, kind=EXCHANGE_KIND, payload=descriptor)


def book_send(tenant: Tenant, ledger: Ledger, xchg: str, peer: str,
              payload) -> Cell:
    """
    Book the SEND half of exchange `xchg` into your own ledger.

    `peer` is the counterpart's tenant identity; `payload` is the thing
    being exchanged (or a descriptor of it). Only its hash is recorded.
    Costs no γ, like LINK — a booking is an observation, not a surprise.
    """
    return _book(tenant, ledger, xchg, peer, payload, "send")


def book_receive(tenant: Tenant, ledger: Ledger, xchg: str, peer: str,
                 payload) -> Cell:
    """
    Book the RECEIVE half of exchange `xchg` into your own ledger.

    `peer` is the sender's tenant identity; `payload` is what you
    actually received. If it differs from what the sender booked, the
    payload hashes will not meet and `agree` will refuse with
    PAYLOAD_MISMATCH — which is the point.
    """
    return _book(tenant, ledger, xchg, peer, payload, "receive")


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
    several or none. `budget` records the budget the law replay assumed.
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

    @property
    def ok(self) -> bool:
        """True when both the chain and the conservation law verify."""
        return self.chain_ok and self.law_ok


def _audit(path: PathLike, budget: float):
    """Audit `path`; return (AuditVerdict, [valid entries as dicts])."""
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
        content = json.dumps(entry, separators=(",", ":"), sort_keys=True)
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
    gamma: dict = {}
    violations: list = []
    excess = 0.0
    for i, e in enumerate(entries):
        ns = e.get("ns", "?")
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
        if g > budget + _EPS:
            excess = max(excess, g - budget)
            violations.append(
                f"entry {i}: gamma={g:.4f} exceeds budget {budget:.4f} "
                f"by {g - budget:.4f} (tenant {ns}, op {e.get('op')})"
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
    )
    return verdict, entries


def audit_ledger(path: PathLike, budget: float = LOG2_3) -> AuditVerdict:
    """
    Audit a foreign ledger from a local copy: hash chain + conservation
    law, as one structured verdict. Reads only; never opens for append.
    Raises FileNotFoundError if the file does not exist.
    """
    verdict, _ = _audit(path, budget)
    return verdict


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
          budget: float = LOG2_3) -> Agreement:
    """
    Decide whether exchange `xchg` is shared state between two ledgers.

    Both ledgers are audited first; a broken chain or a law violation on
    either side refuses everything. The exchange mints only if one side
    booked SEND against the other and the other booked the mirror
    RECEIVE — same exchange id, same payload hash, counterpart identities
    naming each other. Argument order does not matter (the sender is
    whoever booked the SEND), but `head_a`/`entries_a` always describe
    the first argument's ledger.
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
                 budget: float = LOG2_3):
    """
    The derived fold of two ledgers: (agreed, refused) — an Agreement for
    every exchange id booked in either ledger, partitioned by outcome,
    each list sorted by exchange id. The agreed list is the pair's
    shared state; it exists only here, as a fold of the two records.
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
        verdict = agree(path_a, path_b, xchg, budget=budget)
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
