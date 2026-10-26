"""
stitch.stitches — The 11 stitches (operations on cells).

Data plane (5):  bind, link, effect, view, tick
Governance (6):  attest, delegate, contest, merger, revoke, withdraw
"""

from __future__ import annotations
from typing import Any, Optional
import hashlib
import time
import json

from .core import (
    Tenant,
    Cell,
    Ledger,
    Surprise,
    ConservationError,
)
from .exceptions import (
    BindError,
    EffectRejected,
    UnknownCell,
)


# ============================================================================
# Internal helpers
# ============================================================================

def _now_ns() -> int:
    """Current time in nanoseconds since epoch (UTC)."""
    return time.time_ns()


def _cell_id(tenant: str, kind: str, payload: dict, parent_hash: Optional[str], ts: int) -> str:
    """
    Deterministic cell ID. SHA-256 of (tenant:kind:payload:parent_hash:ts), first 16 hex chars.

    Not cryptographically unique (16 hex = 64 bits), but collision-resistant in practice
    for a single ledger. A real deployment would use a longer hash or UUID.
    """
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    # SPEC.md: the parent component is (parent_hash or "") — an absent parent
    # serialises as the empty string, never as Python's "None".
    material = f"{tenant}:{kind}:{payload_json}:{parent_hash or ''}:{ts}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


# ============================================================================
# Data plane stitches (5)
# ============================================================================

def bind(
    tenant: Tenant,
    ledger: Ledger,
    kind: str,
    payload: dict,
    parent_hash: Optional[str] = None,
    cost: float = 0.0,
) -> Cell:
    """
    BIND: create a new cell.

    Charges `cost` bits of γ (default 0 — creating a cell is not surprising).
    Returns the new Cell.

    Raises:
        ConservationError: if the cost would violate the conservation law.
        BindError: if the payload is not JSON-serializable.

    The cell's ID is the first 16 hex chars of SHA-256(tenant:kind:payload:parent_hash:ts).
    The cell's payload is mutable in-memory (use `effect` to record changes to the ledger).
    """
    if not isinstance(payload, dict):
        raise BindError(f"payload must be a dict, got {type(payload).__name__}")
    try:
        # Verify payload is JSON-serializable
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as e:
        raise BindError(f"payload is not JSON-serializable: {e}") from e

    if not tenant.charge(cost):
        raise ConservationError(
            f"Tenant {tenant.identity!r} cannot afford to bind "
            f"(γ={tenant.gamma}, cost={cost}, budget={tenant.budget})"
        )
    ts = _now_ns()
    cell_id = _cell_id(tenant.identity, kind, payload, parent_hash, ts)
    cell = Cell(
        id=cell_id,
        tenant=tenant.identity,
        kind=kind,
        payload=dict(payload),  # copy
        parent_hash=parent_hash,
        created_at=ts / 1e9,
    )
    ledger.append({
        "t": ts,
        "ns": tenant.identity,
        "op": "BIND",
        "cell": cell.id,
        "kind": kind,
        "payload": payload,
        "parent": parent_hash,
        "g": tenant.gamma,
    })
    return cell


def link(
    tenant: Tenant,
    ledger: Ledger,
    cell_a: Cell,
    cell_b: Cell,
    relation: str,
) -> None:
    """
    LINK: declare a relation between two cells. No γ cost (relations are observations).

    The relation is a string (e.g., "depends_on", "spawned_by", "merged_into").
    The semantics are up to the caller — stitch is agnostic.
    """
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "LINK",
        "a": cell_a.id,
        "b": cell_b.id,
        "rel": relation,
        "g": tenant.gamma,
    })


def effect(
    tenant: Tenant,
    ledger: Ledger,
    surprise: Surprise,
    cell: Cell,
    key: str,
    predicted: dict,
    observed: dict,
    strict: bool = False,
) -> bool:
    """
    EFFECT: write to a cell. Charges γ = surprise(predicted, observed).

    Args:
        tenant: The tenant paying the surprise cost.
        ledger: The append-only ledger to record the write to.
        surprise: The surprise oracle (computes KL between predicted and observed).
        cell: The cell to write to.
        key: The payload key to write.
        predicted: What the caller predicted the observation would be.
        observed: What was actually observed.
        strict: If True, raise EffectRejected instead of returning False on
                conservation law violation. Default: False (return False).

    Returns:
        True if the write was committed (γ was charged).
        False if the write was rejected (would violate γ+η≤log₂3).

    The caller must handle the False case — typically by `tick`-ing (resetting γ)
    or by accepting the budget is exhausted for this tick.

    When True:
      - γ is charged
      - cell.payload[key] is mutated in-memory
      - A ledger entry is appended with `s` (surprise bits) recorded.

    When False:
      - γ is unchanged
      - cell.payload is NOT mutated
      - No ledger entry is appended
      - If strict=True, raises EffectRejected instead.
    """
    s = surprise.compute(predicted, observed)
    if not tenant.can_afford(s):
        if strict:
            raise EffectRejected(
                f"EFFECT rejected: would violate γ+η≤log₂3 "
                f"(γ={tenant.gamma}, s={s}, budget={tenant.budget})"
            )
        return False
    # Commit
    if not tenant.charge(s):
        return False  # race-condition guard (shouldn't happen)
    # Mutate cell payload in-memory
    if key in observed:
        cell.payload[key] = observed[key]
    elif key in predicted:
        # Predicted a key that wasn't observed — don't mutate, but log the surprise
        pass
    else:
        cell.payload[key] = None
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "EFFECT",
        "cell": cell.id,
        "k": key,
        "v": observed.get(key),
        "s": s,
        "g": tenant.gamma,
    })
    return True


def view(cell: Cell, key: Optional[str] = None) -> Any:
    """
    VIEW: read from a cell. Free (no γ cost, no ledger entry).

    Reads are not logged. If you need logged reads, use ATTEST with a claim.

    Args:
        cell: The cell to read from.
        key: Optional specific key. If None, returns a copy of the full payload.

    Returns:
        The value at cell.payload[key], or a copy of the full payload if key is None.
    """
    return cell.view(key)


def tick(tenant: Tenant, ledger: Ledger) -> None:
    """
    TICK: advance time. Resets γ for the tenant (conservation law is per-tick).

    After tick(), the tenant's γ is 0 again, and they can produce more surprise
    up to the budget C.

    The ledger is append-only — TICK does not delete prior γ records, it just
    resets the tenant's in-memory counter for the next tick.
    """
    tenant.reset_tick()
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "TICK",
        "g": 0.0,
    })


# ============================================================================
# Governance plane stitches (6)
# ============================================================================

def attest(
    tenant: Tenant,
    ledger: Ledger,
    cell: Cell,
    claim: str,
) -> str:
    """
    ATTEST: sign a claim about a cell.

    v0.1: signature is a SHA-256 hash placeholder. v0.2 will use real Ed25519.

    Args:
        tenant: The attesting tenant.
        ledger: The ledger to record the attestation in.
        cell: The cell the claim is about.
        claim: A string describing the claim (e.g., "completed_turn", "verified_output").

    Returns:
        The signature string (16 hex chars in v0.1).
    """
    # Placeholder — real impl uses Ed25519
    sig_material = f"{tenant.identity}:{cell.id}:{claim}"
    sig = hashlib.sha256(sig_material.encode("utf-8")).hexdigest()[:16]
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "ATTEST",
        "cell": cell.id,
        "claim": claim,
        "sig": sig,
        "g": tenant.gamma,
    })
    return sig


def delegate(
    tenant: Tenant,
    ledger: Ledger,
    cell: Cell,
    to_identity: str,
    scope: str,
) -> None:
    """
    DELEGATE: assign partial agency over a cell to another tenant.

    Args:
        tenant: The delegating tenant (current owner).
        ledger: The ledger to record the delegation in.
        cell: The cell whose agency is being delegated.
        to_identity: The identity of the receiving tenant.
        scope: A string like "read", "write", "attest" — granularity is up to the caller.
    """
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "DELEGATE",
        "cell": cell.id,
        "to": to_identity,
        "scope": scope,
        "g": tenant.gamma,
    })


def contest(
    tenant: Tenant,
    ledger: Ledger,
    cell: Cell,
    reason: str,
) -> None:
    """
    CONTEST: challenge a cell's validity. Does not revoke — triggers a review.

    Downstream readers should check for CONTEST entries when reconstructing state.
    A contested cell is not necessarily invalid — it's flagged for review.
    """
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "CONTEST",
        "cell": cell.id,
        "reason": reason,
        "g": tenant.gamma,
    })


def merger(
    tenant: Tenant,
    ledger: Ledger,
    cell_a: Cell,
    cell_b: Cell,
    kind: str,
    payload: dict,
) -> Cell:
    """
    MERGER: combine two cells into a new cell.

    The new cell's parent_hash is cell_a.id (the chain follows the primary cell).
    The new cell's payload is the `payload` argument (not a merge of cell_a and cell_b's
    payloads — the caller decides what the merged payload looks like).

    Returns the new Cell.
    """
    new_cell = bind(
        tenant, ledger, kind=kind, payload=payload, parent_hash=cell_a.id
    )
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "MERGER",
        "cell": new_cell.id,
        "a": cell_a.id,
        "b": cell_b.id,
        "kind": kind,
        "g": tenant.gamma,
    })
    return new_cell


def revoke(
    tenant: Tenant,
    ledger: Ledger,
    cell: Cell,
    reason: str,
) -> None:
    """
    REVOKE: undo a cell's authority. Does not delete (append-only).

    The cell remains in the ledger; downstream readers should check for REVOKE
    entries when reconstructing state. A revoked cell's data is still there,
    but it should not be trusted or acted upon.
    """
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "REVOKE",
        "cell": cell.id,
        "reason": reason,
        "g": tenant.gamma,
    })


def withdraw(
    tenant: Tenant,
    ledger: Ledger,
    amount: Optional[float] = None,
) -> None:
    """
    WITHDRAW: release budget back to the pool.

    If amount is None, releases all remaining η (headroom).
    Does NOT change γ — it signals the tenant is done for the tick.

    In single-tenant deployments this is a no-op (there's no pool to release to).
    In multi-tenant deployments with a shared budget, this returns η to the pool
    for other tenants to bid on (via Vickrey auction or similar).
    """
    if amount is None:
        amount = tenant.eta
    ledger.append({
        "t": _now_ns(),
        "ns": tenant.identity,
        "op": "WITHDRAW",
        "amount": amount,
        "g": tenant.gamma,
    })
