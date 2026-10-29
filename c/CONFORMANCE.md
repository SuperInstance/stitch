# stitch-c — conformance record

A pure C99 implementation of the stitch **keyless core**, built and
verified on an aarch64 Jetson Orin against the Python reference in
this same repository (`stitch-python/`, SPEC.md). This document is
the process record: what was checked, what matched, what did not,
and what is knowingly not here.

## Scope

In scope (mirrors the Python semantics):

- SHA-256 (in-tree, FIPS 180-4) for payload commitments and cell ids
- Canonical JSON exactly as SPEC pins it: sorted keys, `(",", ":")`
  separators, raw UTF-8 output (`ensure_ascii=False` semantics),
  control-char escaping, and the v0.4 numeric policy on write paths
  (non-finite floats refused; integers exact — int64/uint64, with
  out-of-range integers carried as verbatim decimal text, which is
  what Python's arbitrary-precision ints dump)
- FNV-1a 64 hash chain; ledger append; `Ledger.verify`
- Foreign-ledger audit: chain verification + conservation-law
  replay with position-aware tenant budget declarations
- Bilateral agreement/minting (`agree`, `agreed_state`) with the
  SPEC refusal reasons, and the head-hash anchor (`verify_anchor`)
- The write side: tenants, cells, all 11 stitches, budget
  declarations, and the `book_send`/`book_receive` helpers

Out of scope for this pass, deliberately:

- **Ed25519 / keyed paths.** `attest` writes the v0.1 SHA-256
  placeholder; audits take no `attest_keys`; `agree` performs no
  `booking_keys` enforcement. The seams are where Python has them
  (the `sig` field, the enforcement step between law audit and
  mirror matching), so a crypto module can be added without
  touching the wire format or verdict logic.
- **N-party agreement** (`agree_n` / `agreed_state_n`). Bilateral
  only, as in the v0.2 lineage.

## Method

Conformance is diffed, not asserted. `conformance/gen_fixtures.py`
builds fixtures *with the Python reference* and records the exact
verdicts; `conformance/run.sh` runs the C tool against the same
files and compares bytes. Both directions are exercised:

- **C reads Python:** every fixture ledger below was written by
  Python; the C audit/agree/state/anchor verdicts must equal
  Python's field-for-field, including the human-readable `detail`
  and violation strings.
- **Python reads C:** `stitchc scenario2` writes a 20-entry and a
  6-entry ledger (all 11 stitches, a budget declaration, a refused
  effect, five exchanges, non-ASCII/tricky-float/big-int payloads);
  the files must be **byte-identical** to the Python twin's, and
  Python's own `audit_ledger`/`agree`/`agreed_state` must accept
  them and mint x1/x4, refusing x2/x3/x5.
- **Numbers:** the float formatter is fuzzed against `json.dumps`
  over 200,942 double bit patterns (200,000 random + specials +
  every `10.0**k` for k in [-323, 308] + `2.0**k` samples), and the
  canonicaliser/`payload_hash` over a corpus of adversarial JSON
  values (unicode keys and strings, escapes, the SPEC divergence
  band floats, integers at and beyond the 64-bit edges, `10**30`).

Reproduce with: `make && sh conformance/run.sh`.

## Results (2026-10-29, gcc 13.3.0, aarch64)

**69 checks, 69 passed, 0 failed.** Breakdown:

| Group | Checks | Result |
|---|---|---|
| cross_lang reference vectors (hashes `b513cabe780e2894`, `24c56ec21ef20816`) | 1 | identical |
| cross_lang canonical scenario, byte-compare vs Python | 1 | identical (511 bytes) |
| scenario2 rich pair, byte-compare (alice 20 entries, bob 6) | 2 | identical |
| audit fixtures (13 ledgers × audit + verify) | 26 | verdicts identical |
| rich pair audits (2) + agrees x1..x5,never in both argument orders (12) + state fold (1) | 15 | identical |
| pairs: mint / peer_mismatch / chain_broken / law_violated (agrees both orders + state) | 14 | identical |
| anchors: anchored / truncated / rewritten / chain_broken / missing / zero-pin | 6 | identical |
| float repr corpus (200,942 patterns) | 1 | identical |
| canonicalisation corpus | 1 | identical |
| payload-hash corpus | 1 | identical |
| Python accepts C-written ledgers (audit + agree + fold) | 1 | ok |

The 13 audit fixtures cover: a valid ledger; a tampered line
(chain breaks at the right index); recorded-`g` mismatch; plain
overspend; overspend followed by a larger budget declaration
(position-aware: still refused); declaration-first (lawful);
second declaration ignored; two tenants in one file; integer
(not float) `s`/`g` fields; a NaN payload (chain still verifies —
readers are unpoliced); blank and whitespace-padded lines; a
duplicate-key line (last-wins, hashed over the deduplicated
canonical form); and the empty file.

The Python suite on this branch is unaffected: **181 passed**
(`stitch-python/tests/`), and `tests/cross_lang.py` passes. An
ASan+UBSan build of the C tool runs the scenario/audit/agree/
anchor/phash paths with no sanitizer reports.

## What the corpora caught (fixed before shipping)

1. **Shortest-float formatting.** The first formatter took the
   correctly-rounded `printf("%.*e")` candidate as the shortest
   repr. Five corpus lines refuted it — e.g. `2.0**-24`: Python
   prints `5.960464477539063e-08` (16 digits); the printf candidate
   `...062` does **not** round-trip (below a power of two the ulp
   halves, so the round-trip interval is asymmetric), and the
   formatter fell through to 17 digits. The fix tests the printf
   candidate and both decimal neighbours at each precision and
   picks the closest round-tripper, ties to an even last digit —
   CPython's dtoa rule. Corpus now clean over all 200,942 patterns.
2. **uint64 saturation.** A 20-digit integer token was parsed with
   `strtoull` unchecked: `18446744073709551616` (2^64) saturated
   to `18446744073709551615` instead of falling through to the
   verbatim-text representation. This silently corrupted the
   tricky-payload cell (wrong payload, wrong cell id, broken chain
   at the next entry). Fixed by checking `errno == ERANGE`; the
   canon corpus and scenario2 byte-compare pin it.
3. **Harness bugs (not implementation bugs), for the record:**
   fixture generation was not idempotent (Python `Ledger` appends,
   so a second generation doubled ledgers) and two report diffs
   traced to path-string mismatches in the driver (`pairs/x//a.jsonl`
   vs `pairs/x/a.jsonl` — verdict details embed the path exactly as
   passed, in both implementations). The driver now regenerates
   from scratch and passes canonical relative paths.

Process note: the first transfer of these sources to the build box
went through shell heredocs, which mangled every C char literal
(`'a'` became `"a"`). The sources were rewritten through a
byte-exact channel and rebuilt; the compiler (`-Wall -Wextra
-Werror`) plus this conformance suite are the evidence the shipped
files are the intended ones.

## Known divergences (pathological inputs only)

On every fixture above the implementations agree byte-for-byte.
The following inputs are ones where **Python raises an exception**
and C, which must return a verdict, chooses a defined behaviour.
None occur in ledgers written by either implementation's helpers.

1. A ledger line that parses to a non-object, or an entry whose
   `s`/`g` field is null or a non-numeric string: Python's audit
   raises (`AttributeError`/`TypeError`/`ValueError`). C treats the
   line as a chain break (non-object) or the value as 0.0
   (non-numeric `s`/`g`).
2. A stored `h` that is not a 16-hex string: Python's
   `_compute_last_hash` raises on `int(h, 16)`; C leaves the running
   hash unchanged when reopening such a file for append.
3. A budget declaration whose value is an integer too large for a
   double: Python raises `OverflowError`; C saturates via `strtod`.
4. Lone-surrogate `\uXXXX` escapes in strings: Python carries the
   lone surrogate and fails later at `.encode("utf-8")` while
   hashing; C encodes the code point as UTF-8 (WTF-8) and proceeds.
5. C's write path is **stricter** than raw Python `Ledger.append`:
   `cledger_append` refuses any entry containing a non-finite
   float (SPEC numeric policy — Python enforces the same refusal in
   `book_send`/`book_receive`, but its raw `append`/`bind` would
   write `NaN`). In `cstitch_bind` the refusal surfaces after the
   tenant charge, mirroring how a Python I/O failure would.
6. Verdict `detail` strings are byte-identical on all fixtures.
   For exotic values Python would format with `str()` — e.g. a
   dict used as `ns` — C prints the canonical JSON form instead.
   Reason codes and all structured fields still match.
7. Practical caps: JSON nesting depth 512 (Python's parser limit
   is frame-based and somewhat higher); individual formatted
   fragments inside verdict strings are capped at 511 bytes.
8. The float formatter assumes the C library's `printf`/`strtod`
   are correctly rounded (true for glibc; the 200,942-pattern
   corpus is the empirical check on this platform).

## Build-box notes

Built on the Jetson Orin (7.5 GB RAM, swap full on arrival —
memory-tight by the lane's own report). All work stayed in
`~/stitch-c`; compiles ran at `-j2`; no other lane's processes or
directories were touched. Observed available memory during the
session ranged ~0.7–1.9 GB; the C build itself is small (3,196
lines of C/H total, 142 KB binary) and no build or test failed
from memory pressure.
