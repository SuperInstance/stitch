# WS3 — Integrating signed bookings (Track C) into the repo

**Workstream:** SuperInstance/stitch v0.4, workstream 3.
**Branch:** `v04-ws3` (base `main` @ `d8df453`), pushed to origin.
**Date:** 2026-10-27. Inputs: `~/stitch-v04-input/track-c/` (REPORT.md,
SPEC-DELTA.md, `lab/` prototype + 16 proof tests).

## What was ported

Exactly the prototype's delta — the lab was cut from `fc34d8b`, and the
one commit between `fc34d8b` and `d8df453` touched only docs/tutorials,
so `agreement.py` and `__init__.py` were byte-identical between the two
bases and the delta applied with **no reconciliation**:

- `stitch-python/stitch/agreement.py` — `book_send`/`book_receive` gain
  `secret_key=` (32-byte Ed25519, `ValueError` otherwise); a signed
  booking's descriptor carries one extra key `"sig"` (128 lowercase hex)
  over `booking_signing_material()` (exported): the canonical JSON
  (`"v": "stitch-booking/1"`) of exactly the commitment fields — tenant
  (= the entry's `ns`), xchg, side, peer, ph. `agree`, `agreed_state`,
  `agree_n`, `agreed_state_n` gain `booking_keys=` ({identity: public
  key, 32 raw bytes or 64-char hex}); `_booking_signature_failure()`
  checks EVERY booking for the exchange by a keyed party (not just the
  one that would mirror), after the chain/law audits and before mirror
  matching. New refusal reasons `UNSIGNED_BOOKING = "unsigned_booking"`
  and `BAD_SIGNATURE = "bad_signature"`.
- `stitch-python/stitch/__init__.py` — exports `BAD_SIGNATURE`,
  `UNSIGNED_BOOKING`, `booking_signing_material` (imports + `__all__).
  This file is byte-identical to the lab's.
- `stitch-python/tests/test_signed_bookings.py` — the 16 proof tests
  (impostor refuses `unsigned_booking`/`bad_signature`; still mints with
  no keys; genuine signed pair + 3-party ring mint under enforcement;
  ph-altered-after-signing with honestly recomputed FNV chain refuses;
  wrong-auditor-key, partial-enforcement, key-length, descriptor-shape,
  Key-identity round-trip boundary pins).
- `SPEC.md` — the delta's **"Signed bookings (proposed)"** section
  inserted **verbatim** (verified byte-identical substring) as its own
  section directly after **"Ed25519 signatures (v0.3)"** and before
  "Implementation status", including the verbatim-replay residual
  ("Not ledger provenance") exactly as the delta states it. The diff on
  SPEC.md is pure insertion (191 lines added, 0 removed); no other
  section, the changelog, README, VERIFICATION, versions, or
  `pyproject.toml` were touched.

No 12th stitch: bookings remain `BIND` of kind `"exchange"`; `bind`,
the audit, the anchor, and the verdict dataclasses are untouched.

## Suite results (run on this branch, after integration)

| Check | Result |
|---|---|
| Pre-change baseline (main @ d8df453), both invocations | 150 passed |
| Full suite after integration, from repo root (`python3 -m pytest stitch-python/tests/`) | **166 passed** (150 + 16) |
| Full suite from inside `stitch-python/` (`python3 -m pytest tests/`) | **166 passed** |
| The 150 pre-existing tests modified? | No — `git diff` touches none of them; they pin the no-keys path byte-identical to v0.3 |
| Crypto lazy-import invariant (`stitch.crypto` absent from `sys.modules` after `import stitch`, `import stitch.agreement`) | holds |
| `tests/cross_lang.py` (Python side, byte-exactness vectors) | PASSED |
| `examples/minimal_agent.py` | runs clean |

Environment: Python 3.14.4, pytest 9.1.1 (the lab had used 3.12/9.2.0 —
no difference observed; the suite runs in ~11 s here).

## Deviations from the lab prototype

Code: **none.** `agreement.py` and `__init__.py` were diffed against the
lab copies after the port: `__init__.py` is byte-identical;
`agreement.py` differs in exactly one docstring line (see below). The 16
tests are the lab's, adapted only in their module docstring.

1. **`agreement.py` module docstring, one line.** The lab's line
   "TRACK-C PROTOTYPE (this lab copy only — NOT in the shipped repo):"
   is false once the file IS the shipped repo; it now reads "Signed
   bookings (Track C, ported from the verified lab prototype):". The
   remainder of that paragraph, and every other comment/docstring
   (including the inline "TRACK-C PROTOTYPE"/"TRACK-C:" provenance
   markers), is verbatim — they remain true as provenance.
2. **Test module docstring.** Same issue: "(lab copy only, not
   shipped)" was replaced with the ported-from-lab phrasing, and
   "runs unchanged against this lab copy" now reads "runs unchanged
   (150 passed before and after the patch, in the lab and again
   here)". No test logic, fixture, or assertion changed.
3. **SPEC-DELTA scoping.** Only the delta's drop-in section — from
   "## Signed bookings (proposed)" to the end of the file — was
   inserted. The delta document's own header ("# SPEC delta (DRAFT)")
   and status paragraph were not: they describe the R&D artefact and
   point at `lab/stitch/agreement.py in this directory`, a path that
   does not exist in this repo. REPORT.md identifies the section as
   "the normative section, ready to drop into SPEC.md", which is what
   was dropped in. The section text itself, including the "(proposed)"
   in its title and the replay residual, is verbatim.
4. **VERIFICATION.md not updated.** AGENTS.md asks changers to record
   runs there, but this workstream's brief explicitly forbids editing
   VERIFICATION/README (owned elsewhere); this report is the record
   instead, and the commit body carries the same facts.

## Honest boundaries (unchanged from the lab's verdict)

This closes booking **forgery** (the VERIFICATION §J impostor refusing
under enforcement), not copy authenticity: a genuine signed booking can
still be replayed verbatim into another ledger claiming the same `ns` —
the measured residual, stated in the SPEC section in the delta's own
words. Enforcement is per-party and auditor-opt-in; partial keying
checks only the keyed party (pinned by test). Rust enforcement is
future work; this is Python-first.
