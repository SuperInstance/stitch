"""
stitch — A tiny, language-portable substrate primitive for accountable agent state.

The smallest binding operation in a quilt. 11 stitches, conservation law, append-only
hash-chained ledger, surprise oracle. Zero dependencies.

See SPEC.md for the canonical specification.
See ARCHITECTURE.md for where this fits in the larger Thor synthesis.
"""

from .core import (
    Tenant,
    Cell,
    Ledger,
    Surprise,
    KLSurprise,
    LOG2_3,
    FNV_OFFSET,
    FNV_PRIME,
    MASK_64,
    ConservationError,
    LedgerError,
)
from .exceptions import (
    StitchError,
    BindError,
    EffectRejected,
    UnknownCell,
)
from .stitches import (
    bind,
    link,
    effect,
    view,
    tick,
    attest,
    delegate,
    contest,
    merger,
    revoke,
    withdraw,
    declare_budget,
    TENANT_KIND,
)
from .agreement import (
    EXCHANGE_KIND,
    AGREED,
    NO_SEND,
    MISSING_MIRROR,
    PAYLOAD_MISMATCH,
    PEER_MISMATCH,
    CHAIN_BROKEN,
    LAW_VIOLATED,
    BAD_SIGNATURE,
    UNSIGNED_BOOKING,
    booking_signing_material,
    AuditVerdict,
    Agreement,
    AgreementN,
    AnchorVerdict,
    payload_hash,
    book_send,
    book_receive,
    audit_ledger,
    agree,
    agreed_state,
    verify_anchor,
    agree_n,
    agreed_state_n,
)

__version__ = "0.4.1"
__author__ = "SuperInstance"
__license__ = "MIT"

__all__ = [
    # Constants
    "LOG2_3",
    "FNV_OFFSET",
    "FNV_PRIME",
    "MASK_64",
    # Core
    "Tenant",
    "Cell",
    "Ledger",
    "Surprise",
    "KLSurprise",
    "ConservationError",
    "LedgerError",
    # Exceptions
    "StitchError",
    "BindError",
    "EffectRejected",
    "UnknownCell",
    # Stitches
    "bind",
    "link",
    "effect",
    "view",
    "tick",
    "attest",
    "delegate",
    "contest",
    "merger",
    "revoke",
    "withdraw",
    # Tenant declaration helpers (v0.3 — not new stitches; helpers that use BIND)
    "declare_budget",
    "TENANT_KIND",
    # Agreement (v0.2)
    "EXCHANGE_KIND",
    "AGREED",
    "NO_SEND",
    "MISSING_MIRROR",
    "PAYLOAD_MISMATCH",
    "PEER_MISMATCH",
    "CHAIN_BROKEN",
    "LAW_VIOLATED",
    # TRACK-C PROTOTYPE (signed bookings)
    "BAD_SIGNATURE",
    "UNSIGNED_BOOKING",
    "booking_signing_material",
    "AuditVerdict",
    "Agreement",
    "AgreementN",
    "AnchorVerdict",
    "payload_hash",
    "book_send",
    "book_receive",
    "audit_ledger",
    "agree",
    "agreed_state",
    "verify_anchor",
    # N-party agreement (v0.3 — closes the "bilateral only" gap)
    "agree_n",
    "agreed_state_n",
]
