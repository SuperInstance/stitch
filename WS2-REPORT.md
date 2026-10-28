# WS2 — Numeric byte-exactness: SPEC policy, precision-safe Rust audit, pinned vectors

**Date:** 2026-10-27 · **Branch:** `v04-ws2` (base `main` @ `d8df453`) · **Input:** `~/stitch-v04-input/track-a-REPORT.md` (the 1,884-payload byte-exactness fuzz study)

## What shipped

1. **SPEC.md** — one new section, "Numeric policy (v0.4)", placed immediately after "Byte-exactness" (i.e. after its "Non-ASCII content" subsection, before "Agreement (v0.2)"). No other section touched. It contains: the normative **auditor rule** (verification MUST hash the original line bytes or a canonical form preserving number text verbatim, never a re-serialization of parsed float values; where re-serialization is unavoidable the parser must preserve number text or be correctly rounded), the **writer policy** (non-finite floats MUST NOT be booked; integers outside [−2⁶³, 2⁶⁴−1] MUST be carried as decimal strings; writers SHOULD avoid floats in the measured 1e-9..1e-4 band in commitments, preferring strings or scaled integers), and the measured-evidence table with the concrete examples (`1e-05`→`0.00001`, `123456789.12345679`→`123456789.1234568`, `18446744073709551616`→`1.8446744073709552e+19`, NaN/Infinity unbookable).
2. **stitch-rs** — the audit/verify path made precision-safe by building `serde_json` with the **`arbitrary_precision`** feature (one-line Cargo.toml change + explanatory comments at the two hash-recomputation sites + crate doc note). Four new tests (10 total, all pass).
3. **stitch-python** — `tests/test_numeric_policy.py` (8 tests): canonical float texts of the portable set, parse→reserialize stability of Python's canonical texts, fixed `payload_hash` SHA-256 vectors for the study's key examples, integer boundary pins, distinct-commitment pins for the big-integer and band-float forms, and the `allow_nan=False` rejection semantics the SPEC names.

## The Rust approach and why

The brief offered two routes: hash the original line bytes, or make the parse→re-serialize path exact. **Chosen: `arbitrary_precision`.**

`Ledger::verify` and `audit_ledger` both recompute hashes as parse → strip `h` → re-serialize → FNV. With `arbitrary_precision`, a parsed `Number` carries its **source text**, so re-serializing a canonical line is the identity on bytes — the re-serialization can never reformat a float, land a ulp away, or degrade an out-of-range integer. That satisfies the SPEC's auditor rule ("a canonical form of the record that preserves number text verbatim") without any hand-rolled scanning.

Why not hash the original line bytes directly (the wire format permits it — lines are complete JSON objects):

- Locating the top-level `"h"` key textually requires an escape-aware, nesting-aware scanner: a payload can legitimately contain its own `"h":` at depth > 0, so a substring search is ambiguous. That scanner is a hand-written mini-JSON parser — a larger correctness risk than the bug it fixes.
- Raw-byte hashing changes accept/reject semantics at the margin: Python's own verify canonicalizes before hashing (loads → `sort_keys` → dumps), so it accepts a non-canonical line (e.g. unsorted keys) whose *canonical* form hashes correctly. Re-serialization through serde_json's BTreeMap reproduces exactly that behavior; raw-byte hashing would have rejected lines Python accepts.
- Bonus: `as_f64()` under `arbitrary_precision` parses the stored text with Rust's std parser, which **is** correctly rounded — the conservation-law replay (`s`/`g` accumulation) is unaffected.
- The writer path is untouched: `json!`-constructed floats still format via Ryu, and `test_cross_lang_byte_exact_with_python` still pins the identical hashes and file bytes (`b513cabe780e2894` / `24c56ec21ef20816`) — cross-language writer parity did not move.

### Evidence (red → green, all on serde_json 1.0.154 = the repo's locked version)

Pre-fix probe on this box (default parser, parse → re-serialize):

```
123456789.12345679 -> 123456789.1234568   DRIFT   (±1 ulp, the report's H2)
1e-05              -> 0.00001             DRIFT   (band re-formatting, H1 on the verify path)
1.5e-07            -> 1.5e-7              DRIFT
0.000001           -> 1e-6                DRIFT
{"big":18446744073709551616} -> {"big":1.8446744073709552e+19}  (H3)
```

The four new Rust tests were added **before** the fix and run red against the default parser (6 old passed, 4 new failed — the false-break is real: a Python-written line containing any of these values failed `Ledger::verify`/`audit_ledger`). After enabling the feature: **10/10 pass**. All three ledger-path tests build Python-style canonical lines directly (via a `python_line` helper that FNVs the h-less content and inserts `h` at its sorted position), so they pin the verify/audit path independently of this crate's own writer.

### New finding beyond the input report

`arbitrary_precision`'s capture normalizes a **bare positive exponent**: `1e5` → `1e+5`, `1E5` → `1e+5`, `1.7976931348623157e308` → `1.7976931348623157e+308`. Exponents with an explicit sign (`e+16`, `e-05`, `e-324`) round-trip verbatim — and **Python's `repr` always writes the exponent sign**, so every Python-writable float text is unaffected (this bit the first version of `test_number_text_round_trips_verbatim`, which used the no-plus form; fixed to the Python-canonical `e+308`). Documented in the test comment. Relatedly, the feature also lets the parser *accept* number texts outside f64 range (e.g. hand-crafted `1e999`) that the default parser rejects — unreachable from Python writers and forbidden by the new SPEC policy (non-finite MUST NOT be booked), noted here for completeness.

## Test results (the full AGENTS.md matrix, run 2026-10-27 on this box)

| Check | Result |
|---|---|
| `cargo test` (stitch-rs) | **10 passed** (6 existing + 4 new), 0 failed |
| `pytest stitch-python/tests/` from repo root | **158 passed** (150 existing + 8 new) |
| `pytest tests/` from inside `stitch-python/` (2nd invocation) | **158 passed** |
| `python tests/cross_lang.py` | PASSED (reference vectors `b513cabe780e2894`/`24c56ec21ef20816` asserted; Rust side asserts identical bytes) |
| `examples/minimal_agent.py` | OK |
| `examples/cross_instance.py make --identity alice --dir …` + `audit` | chain=OK law=OK |
| `examples/cross_instance2.py` (sender/receiver pair + derive) | MINT x-agree, REFUSE x-dispute `payload_mismatch` — both by design |
| `examples/functional_scenario.py` (3-self world: make ×3 + derive) | exit 0; signed/wrong-key/unsigned verdicts and the documented impostor limitation all as designed |
| `tutorials/01-accountable-journal` (`work.py` + `audit.py`) | AUDIT: PASS |
| `tutorials/02-agent-handoff/run_demo.sh` | happy path mints (exit 0); tampered-in-transit refuses `payload_mismatch` (exit 1) — the demo's designed outcome |
| `tutorials/03-corroborated-readings/run_demo.sh` | quorum reached, exit 0 |

Note on the examples: invoked bare, `cross_instance*.py` and `functional_scenario.py` exit 2 — that is argparse usage-error behavior (they are subcommand CLIs), **identical on the pristine base** (verified against `d8df453` via a temporary worktree/stash); the table above shows them run with their documented invocations.

## Divergences from the brief

- **No Python library code changed.** `payload_hash` still accepts NaN/Infinity today; the SPEC's rejection rule for booking helpers is written as SHOULD (matching the input report's recommendation), and `test_numeric_policy.py` pins the stdlib `allow_nan=False` semantics the enforcement will use — deliberately NOT asserting the current permissive behavior, so a later enforcement change doesn't fight these tests.
- **Four Rust tests** rather than three: the brief named three pinned cases (round-trip, `1e-05`, >u64 line); a fourth (`test_number_text_round_trips_verbatim`) pins the verbatim-preservation mechanism itself across the portable number set, which is what the other three rest on.
- **VERIFICATION.md not updated** (the brief forbade editing it) although AGENTS.md's convention is to record runs there; the run record lives in this report instead.
- No version bumps, no README/`agreement.py`/crypto edits — per the brief. Cargo.lock is unchanged (a feature flag on an already-locked dependency doesn't move it).

## Transport

Committed on `v04-ws2`. Push attempted to `origin`; if it failed (no credentials for SuperInstance on this box), the branch is exported to `~/stitch-v04-ws2.bundle`.
