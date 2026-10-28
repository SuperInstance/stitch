# WS1 — fast Ed25519 integrated into `stitch.crypto`

Branch `v04-ws1` off `main` (d8df453), 2026-10-27. One file changed:
`stitch-python/stitch/crypto/ed25519.py` (plus this report).

## What changed

The affine point arithmetic was replaced by the verified prototype
(`~/stitch-v04-input/ed25519_fast.py`, proto1 / Track B experiment 2):

- **Extended twisted-Edwards coordinates** (X, Y, Z, T), Hisil–Wong–
  Carter–Dawson unified addition and dedicated doubling. Scalar
  multiplication performs **no modular inversions**; the one remaining
  inversion is when a point is compressed to bytes. The old code did
  two inversions (each a full 255-bit exponentiation) per point
  addition, ~380 additions per scalar multiplication.
- **Windowed fixed-base table for B** (window 4, `table[i][j] =
  j·16^i·B`, built once at import, ~15 ms): `[s]B` becomes 64 table
  lookups + additions, zero doublings. Signing uses only fixed-base
  multiplications; verify adds one variable-base `[k]A`
  (double-and-add, extended coordinates).
- **Verify compares projectively** (`X1·Z2 == X2·Z1` and `Y1·Z2 ==
  Y2·Z1`) instead of compressing both sides and comparing bytes.
  Equivalent accept/reject: compression is canonical, so
  compress-equality and projective equality agree on all points the
  arithmetic can produce.

**Kept exactly as before** (API and semantics, not just shape):
`secret_key_bytes` / `public_key` / `sign` / `verify` signatures,
error messages and docstrings; the `Key` class (byte-for-byte);
module constants `P, L, D, I, BY, BX, B, IDENTITY` in their affine
forms (`tests/test_ed25519.py` and `crypto/__init__.py` import them);
`_xrecover` and `_point_decompress` line-for-line — deliberately,
so accept/reject semantics, including the docstring's two recorded
soft spots (no small-order subgroup check; the x = 0 / sign = 1
decompress edge), are unchanged. Stdlib only; `stitch.crypto` still
imports nowhere at package load (verified: `import stitch` leaves
`stitch.crypto` out of `sys.modules`).

The module docstring's Performance paragraph was rewritten (it
documented ≈270 ms per op); the soft-spots paragraph stands as it did.

## Adaptations from the prototype, and why

1. **`_point_decompress` returns the affine `(x, y)`** as in the
   shipped module, with `_to_ext` lifting it to extended coordinates
   in `verify`. The prototype decompressed straight to extended; I
   kept the shipped function line-for-line so the decompress path
   (the documented soft spot) is provably untouched.
2. **Old private helpers were re-homed, not deleted-as-API**: `B`,
   `BX`, `BY`, `IDENTITY` stay at module level in affine form for the
   tests/`__init__` imports; the new internals (`IDENTITY_EXT`,
   `_ext_*`, `_fixed_scalar_mult`, `_point_compress_ext`,
   `_TABLE`) follow the module's existing naming and section-banner
   layout.
3. Prototype's `_recover_b()` was dropped — the module already
   computes `BX`/`BY` with identical root selection, so the table is
   built from those (`_B_EXT = _to_ext(BX, BY)`); one source of truth.

## Gates (all pass)

| Gate | Result |
|---|---|
| Pinned RFC 8032 §7.1 vectors (`tests/test_rfc8032_vectors.py`) | 6/6 pass |
| Full suite from repo root (venv, pytest 9.2.0): `.venv/bin/python -m pytest stitch-python/tests/` | **150 passed in 0.34 s** |
| Full suite, second invocation (from `stitch-python/`) | 150 passed in 0.22 s |
| Differential vs OLD module (saved copy, `~/stitch-v04-scratch/ed25519_old.py`): 25 random keys/messages — public keys and signatures **byte-identical**, cross-verify both directions, tampered signature AND tampered message rejected by both | ALL PASS |
| Opt-in invariant (`import stitch` does not load crypto) | PASS |
| Extra (AGENTS.md matrix): `tests/cross_lang.py`, examples (`minimal_agent`, `cross_instance` make/audit, `cross_instance2`, `functional_scenario` make×3/derive), all three tutorials' demos | all PASS |

The differential script is `~/stitch-v04-scratch/diff_check.py`
(outside the repo; it also re-runs the RFC vectors against the new
module from the pinned constants).

## Timing smoke (this box: ARM64 Tegra, mean of 20, same key/message)

| | sign | verify |
|---|---|---|
| old (v0.3 affine) | 190.9 ms | 193.1 ms |
| new (this branch) | **0.78 ms** | **2.32 ms** |
| speedup | ~244× | ~83× |

(The task brief's ~300 ms / ~1.2 ms / ~3.6 ms were from the
prototype-authoring box; this box measures old ~191 ms, new
0.78/2.32 ms — same conclusion, two orders of magnitude.)

## Not touched (other workstreams own these)

Version numbers, README, VERIFICATION.md, SPEC.md, agreement.py.
`VERIFICATION.md` was NOT updated with this run per the task brief;
this report is the record.
