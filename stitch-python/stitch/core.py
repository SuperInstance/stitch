"""
stitch.core — Core data structures and the conservation law.

This module is the load-bearing wall of the library. Everything else is sugar.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Iterator, Dict
import json
import os
import time


# ============================================================================
# Constants (canonical — see SPEC.md)
# ============================================================================

LOG2_3: float = 1.584962500721156  # log2(3) ≈ 1.585 — the per-tick per-tenant budget
FNV_OFFSET: int = 0xCBF29CE484222325  # FNV-1a 64-bit offset basis
FNV_PRIME: int = 0x100000001B3  # FNV-1a 64-bit prime
MASK_64: int = 0xFFFFFFFFFFFFFFFF
LEDGER_VERSION: int = 1

# Float comparison epsilon — for the conservation law check
_EPSILON: float = 1e-12


# ============================================================================
# Exceptions
# ============================================================================

class ConservationError(Exception):
    """Raised when an operation would violate γ + η ≤ log₂3."""
    pass


class LedgerError(Exception):
    """Raised on ledger I/O or integrity failures."""
    pass


# ============================================================================
# Tenant — the unit of accountability
# ============================================================================

@dataclass
class Tenant:
    """
    A tenant is the unit of accountability. Each tenant has a γ+η≤log₂3 budget per tick.

    Identity is a string. Since v0.3 it may optionally be an Ed25519
    public key in 64-char hex (see stitch.crypto.Key.identity), so that
    signed attestations can be tied to the tenant that made them; key
    to identity binding is still established out of band.

    Attributes:
        identity: A unique string identifying this tenant.
        budget: The per-tick conservation budget C (default: log₂3).
        gamma: Cumulative surprise dissipated this tick (γ).
        pending: Speculative pre-charge (γ_pending) — refundable before commit.

    Properties:
        eta: Headroom remaining = C - γ - pending (η).

    The conservation law: γ + η ≤ C, where η = C - γ - pending (always ≥ 0).
    """
    identity: str
    budget: float = LOG2_3
    gamma: float = 0.0
    pending: float = 0.0

    @property
    def eta(self) -> float:
        """Headroom remaining: η = C - γ - pending."""
        return max(0.0, self.budget - self.gamma - self.pending)

    def can_afford(self, cost: float) -> bool:
        """Check if a write costing `cost` bits of surprise would fit in the budget."""
        return self.gamma + self.pending + cost <= self.budget + _EPSILON

    def charge(self, cost: float) -> bool:
        """
        Charge γ by `cost` bits. Returns True if it fit, False if it would violate
        the conservation law. Does NOT raise — callers should handle the False case.

        For most use cases, use the `effect()` stitch which calls this internally.
        """
        if not self.can_afford(cost):
            return False
        self.gamma += cost
        return True

    def pre_charge(self, cost: float) -> bool:
        """
        Speculatively pre-charge γ_pending by `cost`. Used for operations that
        might roll back. Refund with `refund_pending`, commit with `commit_pending`.
        """
        if self.gamma + self.pending + cost > self.budget + _EPSILON:
            return False
        self.pending += cost
        return True

    def commit_pending(self, actual: float) -> None:
        """
        Convert pending pre-charge to actual γ, adjusting for the real cost.

        Releases all pending, charges `actual` against γ. If `actual` would
        violate the conservation law, raises ConservationError.
        """
        self.pending = 0.0
        if not self.charge(actual):
            raise ConservationError(
                f"Commit would violate conservation law "
                f"(γ={self.gamma}, actual={actual}, budget={self.budget})"
            )

    def refund_pending(self) -> None:
        """Release all pending pre-charge (operation rolled back)."""
        self.pending = 0.0

    def refund(self, amount: float) -> None:
        """Refund a charged amount of γ (e.g., for a rolled-back EFFECT)."""
        self.gamma = max(0.0, self.gamma - amount)

    def reset_tick(self) -> None:
        """At each TICK, γ resets. The conservation law is per-tick."""
        self.gamma = 0.0
        self.pending = 0.0

    def __repr__(self) -> str:
        return (
            f"Tenant(identity={self.identity!r}, budget={self.budget}, "
            f"γ={self.gamma:.4f}, η={self.eta:.4f}, pending={self.pending:.4f})"
        )


# ============================================================================
# Cell — the smallest unit of accountable state
# ============================================================================

@dataclass
class Cell:
    """
    A cell is the smallest unit of accountable state.

    Attributes:
        id: 16-char hex string (first 16 chars of SHA-256 of creation material).
        tenant: The tenant identity that owns this cell.
        kind: A string categorizing the cell (e.g., "task", "memory", "skill").
        payload: Arbitrary JSON-serializable dict.
        parent_hash: The hash of the parent cell (for chains), or None.
        created_at: Unix timestamp (seconds, float) of cell creation.

    The payload is mutable in-memory (use `effect` to record changes to the ledger).
    The ledger is the source of truth; the in-memory Cell is for convenience.
    """
    id: str
    tenant: str
    kind: str
    payload: Dict[str, Any]
    parent_hash: Optional[str]
    created_at: float

    def view(self, key: Optional[str] = None) -> Any:
        """Convenience read method. Reads are free (no γ cost, not logged)."""
        if key is None:
            return dict(self.payload)
        return self.payload.get(key)

    def set(self, key: str, value: Any) -> None:
        """In-memory mutation. Use `effect()` to record changes to the ledger."""
        self.payload[key] = value

    def __repr__(self) -> str:
        return (
            f"Cell(id={self.id!r}, tenant={self.tenant!r}, kind={self.kind!r}, "
            f"payload_keys={list(self.payload.keys())})"
        )


# ============================================================================
# Surprise oracle — the conservation law enforcer
# ============================================================================

class Surprise:
    """
    Base class for surprise oracles. Subclasses implement `compute`.
    The default is KLSurprise (discrete KL over key agreement).

    The surprise oracle is the SEAM — production deployments can swap in:
      - real KL divergence over continuous distributions
      - hex-lattice deformation (ExoJ pattern)
      - SVGD particle set divergence
      - learned surprise models
    """
    def compute(self, predicted: dict, observed: dict) -> float:
        raise NotImplementedError


class KLSurprise(Surprise):
    """
    Default surprise oracle: discrete KL divergence over key agreement.

    For each key in predicted ∪ observed:
      - both, equal values: 0 bits
      - both, different values: 1 bit
      - observed only: 1 bit (new information)
      - predicted only: 0 bits (predicted was over-complete)

    This is intentionally simple. It is the v0.1 default. Swap freely.
    """

    def compute(self, predicted: dict, observed: dict) -> float:
        all_keys = set(predicted.keys()) | set(observed.keys())
        if not all_keys:
            return 0.0
        surprise = 0.0
        for k in all_keys:
            p_in = k in predicted
            o_in = k in observed
            if p_in and o_in:
                if predicted[k] != observed[k]:
                    surprise += 1.0
            elif o_in and not p_in:
                surprise += 1.0
            # predicted-only: no surprise
        return surprise


# ============================================================================
# Ledger — append-only JSONL with FNV-1a 64-bit hash chain
# ============================================================================

class Ledger:
    """
    Append-only JSONL ledger with FNV-1a 64-bit hash chain.

    Each entry is one JSON object on one line, sorted keys, no whitespace.
    Each entry's `h` field is the FNV-1a 64-bit hash of this entry's content,
    seeded with the previous entry's hash. The first entry is seeded with
    FNV_OFFSET.

    This makes the ledger tamper-evident: changing any entry invalidates all
    subsequent hashes.

    Usage:
        ledger = Ledger("./state.jsonl")
        ledger.append({"t": ..., "ns": "alice", "op": "BIND", ...})
        assert ledger.verify()  # check the hash chain
        for entry in ledger.read_all():
            print(entry)
    """

    def __init__(self, path: str):
        self.path = path
        self._last_hash: int = self._compute_last_hash()
        # Open in append mode (will create if not exists)
        self._f = open(path, "a", encoding="utf-8")

    def _compute_last_hash(self) -> int:
        """Walk the existing ledger to find the last hash, or use FNV_OFFSET."""
        if not os.path.exists(self.path):
            return FNV_OFFSET
        last = FNV_OFFSET
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        h_str = entry.get("h")
                        if h_str is not None:
                            last = int(h_str, 16)
                        else:
                            # Entry without hash — recompute (defensive)
                            last = self._fnv1a(line, last)
                    except json.JSONDecodeError:
                        # Skip malformed lines (or raise — your call)
                        continue
        except FileNotFoundError:
            return FNV_OFFSET
        return last

    @staticmethod
    def _fnv1a(data: str, seed: int) -> int:
        """FNV-1a 64-bit hash of a string, seeded with the previous hash."""
        h = seed & MASK_64
        for b in data.encode("utf-8"):
            h ^= b
            h = (h * FNV_PRIME) & MASK_64
        return h

    def append(self, entry: dict) -> str:
        """
        Append an entry to the ledger. Returns the entry's hash (16 hex chars).

        The entry must NOT have an `h` field — this method computes and adds it.
        The entry's other fields are serialized with sorted keys and no whitespace.

        Raises LedgerError if the entry already has an `h` field.
        """
        if "h" in entry:
            raise LedgerError("Entry must not have an `h` field — append computes it")
        # Serialize without the hash, sorted keys, no whitespace.
        # ensure_ascii=False pins the encoding: non-ASCII content is emitted
        # as raw UTF-8 bytes, matching serde_json on the Rust side. This is
        # the v0.3 byte-exactness pin (see SPEC.md "Byte-exactness").
        line_without_hash = json.dumps(entry, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        # Compute hash seeded with previous hash
        h = self._fnv1a(line_without_hash, self._last_hash)
        h_str = f"{h:016x}"
        # Add hash and re-serialize (sorted keys still)
        entry_with_hash = dict(entry)
        entry_with_hash["h"] = h_str
        line_with_hash = json.dumps(entry_with_hash, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        # Write
        self._f.write(line_with_hash + "\n")
        self._f.flush()
        # Update last hash
        self._last_hash = h
        return h_str

    def read_all(self) -> Iterator[dict]:
        """Read all entries from the ledger, yielding dicts."""
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)

    def verify(self) -> bool:
        """
        Verify the hash chain. Returns True if all hashes are consistent.

        Walks the ledger, recomputes each hash from the entry's content (excluding `h`)
        seeded with the previous hash, and checks it matches the stored `h`.
        """
        last = FNV_OFFSET
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        return False
                    h_str = entry.pop("h", None)
                    if h_str is None:
                        return False
                    line_without_hash = json.dumps(
                        entry, separators=(",", ":"), sort_keys=True,
                        ensure_ascii=False,
                    )
                    expected = self._fnv1a(line_without_hash, last)
                    if f"{expected:016x}" != h_str:
                        return False
                    last = int(h_str, 16)
        except FileNotFoundError:
            return True  # empty ledger is valid
        return True

    def close(self) -> None:
        """Close the underlying file handle."""
        if not self._f.closed:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self) -> str:
        return f"Ledger(path={self.path!r}, last_hash={self._last_hash:#018x})"
