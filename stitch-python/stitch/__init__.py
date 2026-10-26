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
)

__version__ = "0.1.0"
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
]
