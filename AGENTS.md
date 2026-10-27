# AGENTS.md — working in this repo, for agents (and the humans who send them)

You have arrived cold at `SuperInstance/stitch`. This file is the map.
It was written after a cold test (2026-10-27, see VERIFICATION.md)
showed exactly where a new agent gets stuck; every claim below was
re-verified by running it.

## What this is, in one paragraph

`stitch` is a tiny, zero-dependency library for **accountable agent
state**: 11 append-only operations ("stitches") on hash-chained JSONL
ledger files, a per-tenant conservation law (γ + η ≤ log₂3 per tick)
enforced at write time, and **agreement** — shared state that is
*minted* only where two or more sovereign ledgers, written
independently, turn out to record the same exchange. No coordinator,
no shared clock, no server. Ledgers sync by plain file copy.

## Read in this order

1. **README.md** — the concepts and the two copying-grade recipes
   (Quick start, Agreement). Enough to write a working two-agent
   program.
2. **tutorials/** — three complete templates for real jobs (accountable
   journal, agent-to-agent handoff, corroborated observers). If your
   task resembles one of them, start from its code, not from scratch.
3. **SPEC.md** — the canonical wire format and semantics, including the
   v0.3 sections (budget declarations, N-party agreement, Ed25519).
   Read a section before relying on it; formulas there are normative.
4. **VERIFICATION.md** — what has actually been run, where, and what
   failed on the way. Trust this over any summary, including this file.
5. `research/` is the **historical** design record (2026-10-26 scouting
   synthesis and worklog). It explains *why*; it is not instructions,
   and parts of it describe systems that are not in this repo.
   ARCHITECTURE.md is likewise a map of the larger design stitch sits
   in — the other named systems (quilt-*, Border Collie, serving
   ladder) are **not dependencies** and not present here.

## Repo map

| Path | What it is |
|---|---|
| `stitch-python/stitch/` | The Python reference implementation (stdlib only). `core.py` tenants/cells/ledgers, `stitches.py` the 11 stitches, `agreement.py` audit/agree/anchor, `crypto/` opt-in Ed25519 |
| `stitch-python/tests/` | The 150-test suite (pytest). `tests/cross_lang.py` at repo root holds the Python↔Rust vectors |
| `stitch-python/examples/` | Runnable demos; README's Examples table gives each one's invocation |
| `stitch-rs/` | Partial Rust port: ledger, hash chain, `bind`, v0.2 audit. 6 tests. Minting and v0.3 features are Python-first |
| `tutorials/` | Working templates for real jobs — start here for applied work |
| `SPEC.md` / `VERIFICATION.md` / `ARCHITECTURE.md` | Spec / test record / design context |

## Run everything (verified commands)

```bash
python3 stitch-python/examples/minimal_agent.py            # demo
python3 -m venv .venv && .venv/bin/pip install pytest      # suite needs pytest
.venv/bin/python -m pytest stitch-python/tests/            # 150 tests, from root or inside stitch-python/
(cd stitch-rs && cargo test)                               # 6 Rust tests (optional)
bash tutorials/02-agent-handoff/run_demo.sh                # a tutorial end-to-end
```

Install the library with `pip install ./stitch-python` (works), copy
the `stitch/` package into your project, or set
`PYTHONPATH=…/stitch-python`. It is **not on PyPI** yet.

## The five traps that bite cold agents (all cold-test findings)

1. **Two budget knobs.** `Tenant(budget=C)` is enforced at write time;
   `declare_budget(C)` only records C in the ledger for auditors.
   Declaring does not raise your enforced budget. Set both, same value.
2. **The namespace-folder trap.** A directory named `stitch` on your
   path (e.g. this repo's root) can shadow the library import and fail
   later with `AttributeError: … no attribute 'Tenant'`. If imports
   behave strangely, check `stitch.__file__`.
3. **`verdict.reason` values are lowercase strings** (`"payload_mismatch"`),
   while the exported constants are uppercase names
   (`stitch.PAYLOAD_MISMATCH == "payload_mismatch"`). Compare against
   the constant or the lowercase string, never the name.
4. **Crypto messages are bytes.** `Key.sign(b"…")`, not `sign("…")` —
   a `str` raises `TypeError`. Signing is opt-in:
   `attest(tenant, ledger, cell, claim, secret_key=sk)` — note the
   argument order, **cell before claim** — and verification is
   `audit_ledger(path, attest_keys={identity: pubkey})`, judged by
   `verdict.signatures_ok`, which is separate from `verdict.ok`.
5. **`verify()` alone is not an audit.** The chain check passes for a
   hand-built ledger that violates the conservation law. Use
   `audit_ledger` (chain + law replay) wherever a verdict matters.

## Invariants — do not break these

- **11 stitches, no more.** v0.2/v0.3 features were added *without* new
  stitches (exchange bookings and budget declarations are `bind`s with
  reserved kinds). Keep it that way.
- **Byte-exactness is the cross-language contract.** Canonical JSON is
  `json.dumps(..., sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)` — raw UTF-8, never `\uXXXX` escapes. Changing
  serialisation anywhere invalidates every existing ledger and hash.
- **Zero dependencies** in the Python implementation (stdlib only) and
  no imports of `stitch.crypto` at module load — crypto stays opt-in
  so keyless behaviour remains byte-identical to v0.2.
- **Defaults are behaviour.** When a feature is off, entries and
  verdicts must stay byte-identical to the previous version; tests pin
  this. New parameters default to off/None.
- **Append-only.** Nothing rewrites or deletes ledger history; refusal
  paths append nothing and mutate nothing. If your change makes a
  rejection write something, it is wrong.

## Never overclaim (the honest boundaries, also in SPEC)

Agreement proves two **records** agree — not that a copy is authentic,
a payload moved, or a reading is true. Identity is a string claim; a
fresh impostor ledger that mirrors a booking **will** mint against a
genuine one (demonstrated in VERIFICATION.md). Only ATTEST entries can
be signed; bookings are unsigned. FNV-1a is a tamper-evident canary,
not a cryptographic hash. Budgets and surprise inputs are
self-declared: auditable, not honest. Rust audits v0.3 ledgers against
the assumed budget (declarations are Python-first). If your change
touches any of these, state the resulting boundary in the same words
the docs use — do not soften it.

## If you change code

Run the full matrix before claiming anything: the 150-test suite (both
invocations), `tests/cross_lang.py`, `cargo test`, the examples, and
every tutorial's demo script. Record what you ran in VERIFICATION.md
with the date, including failures — this repo's convention is that the
record includes the misses. Version bumps touch `pyproject.toml` and
`stitch/__init__.py` together.
