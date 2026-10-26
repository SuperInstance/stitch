"""
stitch.exceptions — Exception classes for the stitch library.
"""


class StitchError(Exception):
    """Base class for all stitch exceptions."""


class BindError(StitchError):
    """Raised when a BIND operation fails (e.g., bad payload, missing parent)."""


class EffectRejected(StitchError):
    """Raised by strict-mode effect() when the conservation law would be violated.

    Note: the default effect() returns False (does not raise) for the common case.
    This exception is for callers who want strict-mode behavior via effect_strict().
    """


class UnknownCell(StitchError):
    """Raised when a stitch references a cell that doesn't exist."""


class UnknownTenant(StitchError):
    """Raised when a stitch references a tenant that doesn't exist."""


class LedgerIntegrityError(StitchError):
    """Raised when the ledger's hash chain is broken."""
