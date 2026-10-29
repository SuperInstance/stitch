# stitch-c — the stitch keyless core in pure C99

A C99 port of the stitch core (see `../SPEC.md`), covering the
keyless surface: canonical JSON, the FNV-1a hash chain, ledger
append/verify, the conservation-law audit with position-aware
budget declarations, bilateral agreement/minting, the head-hash
anchor, and the 11 stitches on the write side.

- No dependencies beyond libc/libm.
- Ed25519 keyed paths (signed attestations, signed bookings,
  auditor key enforcement) are **not** in this pass; `attest`
  writes the v0.1 SHA-256 placeholder. N-party agreement is also
  not ported. See `CONFORMANCE.md` for the exact scope and the
  known divergences (all on pathological inputs where Python
  raises and C returns a verdict).

## Build

```sh
make            # produces ./stitchc (gcc -std=c99 -Wall -Wextra -Werror)
```

## The `stitchc` harness

Read-side commands print stable `key=value` reports (the
conformance driver diffs them against Python verdicts):

```sh
stitchc audit  <ledger.jsonl> [budget]
stitchc verify <ledger.jsonl>
stitchc agree  <a.jsonl> <b.jsonl> <xchg> [budget]
stitchc state  <a.jsonl> <b.jsonl> [budget]
stitchc anchor <ledger.jsonl> <head|-> <pinned-count>
stitchc phash            # JSON payload on stdin -> payload hash
stitchc canon            # JSON on stdin -> canonical form
```

Deterministic write-side demonstrations (fixed timestamps, so the
output is byte-comparable with the Python reference):

```sh
stitchc append-ref <file>   # the tests/cross_lang.py vectors
stitchc scenario  <path>    # the cross_lang canonical sequence
stitchc scenario2 <dir>     # rich alice/bob pair (Python twin in
                            # conformance/gen_fixtures.py)
```

## Conformance

```sh
sh conformance/run.sh   # regenerates fixtures with Python, diffs all
```

69 checks: byte-identical ledger production in both directions,
verdict-identical audit/agree/anchor over the fixture battery, a
200,942-pattern float-formatting corpus, and canonicalisation /
payload-hash corpora. Full record: `CONFORMANCE.md`.

Library use: `src/stitch.h` is the API (ledger, audit, agreement,
tenants, stitches); `src/json.h` the JSON value model; `src/sha256.h`
the in-tree SHA-256. Write functions take explicit nanosecond
timestamps — Python reads the wall clock, and explicit `ts` is what
makes cross-language byte-exact fixtures possible.
