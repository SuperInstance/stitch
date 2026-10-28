# TIGHTEN-REPORT — backend tightening (2026-10-27)

Branch `backend-tighten` off main `e376e9b`. Four tasks: numeric-policy
enforcement, CI, dist readiness, and a recon on `effect()`'s key-absent
behaviour. No version bump, no README/VERIFICATION edits (release
assembly is Muse's).

## Test counts

| | Before | After |
|---|---|---|
| Python suite (`pytest stitch-python/tests/`, repo root) | 174 passed | **181 passed** |
| Python suite (from inside `stitch-python/`) | — | 181 passed |
| `cargo test` (stitch-rs) | 10 passed | 10 passed |
| `tests/cross_lang.py` | PASSED | PASSED |
| examples / tutorial demos | — | minimal_agent rc 0; tutorials 02 + 03 demos rc 0 |

The 174 pre-existing tests pass **unmodified** — keyless behaviour for
finite payloads is byte-identical, as the invariants require.

## Task 1 — numeric policy enforced at booking time (commit 832fdef)

`agreement.py` gains a private `_reject_non_finite()` (recursive over
dicts incl. keys, lists, tuples; `math.isfinite`). `book_send` and
`book_receive` call it first — before `_book`, before signing, before
any ledger write. SPEC.md's writer-policy sentence updated from
"booking helpers SHOULD reject them" to state the helpers DO reject
with `ValueError`, with the reader asymmetry in the same paragraph.

**Refusal byte-identity evidence** (`test_numeric_enforcement.py`, 7
tests): a ledger containing a real booking is hashed/read before and
after six refused booking attempts (NaN top-level, `{"a": [Inf]}`,
`{"a": {"b": NaN}}` × send/receive) — file bytes **identical**, entry
count unchanged. The signed path refuses before signing with the same
property. Finite edges (1e-05, −0.0, 1e308) still book and mint
through `agree()`. The deliberate asymmetry is pinned, not just
claimed: `payload_hash({"v": NaN})` still equals the v0.4 digest of
`{"v":NaN}` — `payload_hash`/`agree`/audit were not touched.

## Task 2 — CI (commit 76a41fc)

`.github/workflows/ci.yml`: push-to-main + pull_request. Python job
(3.12): `pip install "./stitch-python[test]"`, import smoke printing
`stitch.__version__`, suite from repo root. Rust job: `cargo test`
in `stitch-rs`, then `python3 tests/cross_lang.py` (that harness is
Python-only — it prints "run cargo test for the Rust side" rather
than shelling out; read before wiring).

**Every CI command was run locally first**, in a fresh venv at the
commit: install OK → `0.4.0` printed → 181 passed → cross_lang
PASSED → cargo 10 passed. YAML validated by parsing with PyYAML
(jobs `python`, `rust`; triggers push/pull_request). actionlint is
not installed on this box — that check is noted, not faked.

## Task 3 — dist readiness (no upload)

Built with `python -m build` (build 1.x in a scratch venv, `--outdir`
outside the tree):

| Artifact | Bytes |
|---|---|
| `stitch_substrate-0.4.0.tar.gz` (sdist) | 56,492 |
| `stitch_substrate-0.4.0-py3-none-any.whl` (wheel) | 32,314 |

The **wheel** was installed into a second fresh venv and smoked from
`/tmp` (so imports resolved to site-packages, proven via
`stitch.__file__`): `import stitch` → 0.4.0; two tenants book +
`agree()` a tiny exchange → **MINTED**. The package is dist-ready;
publishing remains Casey's call, untouched here.

## Task 4 — recon: what depends on effect()'s key-absent behaviour?

**Short answer: nothing in the repo.** A refusal change scoped to
"key absent from both dicts" would break zero tests, zero tutorials,
zero examples.

Method: grepped every `effect(` call site in `stitch-python/tests/`,
`tutorials/`, `stitch-python/examples/`, and `tests/cross_lang.py`
(35 call sites), and read the dict arguments at each; grepped tests
for any assertion pinning `v: null` on an EFFECT entry.

Findings:

- **Every call site passes the key in BOTH dicts.** Tests:
  test_budget.py (12 calls, `"k"`/`"k1"`/`"k2"` with matching dicts),
  test_core.py (9 calls, `"status"`/`"k"` with matching dicts,
  incl. :413/:424), test_byte_exact.py:130, test_non_ascii.py:85,181 —
  all keyed identically on both sides. Tutorials: only
  `tutorials/01-accountable-journal/work.py:48,58,65` (`"actions"`,
  `"verdict"`, present in both). Examples (minimal_agent.py:81,108,120;
  cross_instance.py:63; functional_scenario.py:71,159–186) and
  tests/cross_lang.py:69 — same pattern throughout.
- **No test pins `v: null`.** No assertion anywhere reads an EFFECT
  entry's `"v"` expecting None/null. (The `"v"` hits in tests are
  unrelated: hand-built entries in test_agreement.py and
  payload_hash vectors in test_numeric_policy.py.)
- **Where the behaviour lives:** `stitches.py`, `effect()` — when
  the key is in neither dict it still charges γ over the full dicts,
  sets `cell.payload[key] = None`, and appends an entry with
  `"v": observed.get(key)` → JSON `null`, returning True. Note the
  neighbouring, distinct case: key in `predicted` only commits with
  `v: null` too but does NOT mutate the cell ("predicted a key that
  wasn't observed"). That branch is also unexercised by the suite,
  but it is semantically different — a refusal scoped to
  absent-from-both leaves it intact.
- The only other trace of the behaviour is prose: README's stitch
  table ("the entry records `v = observed[key]`… one call per fact")
  and the cold-test-#2 documentation of the one-key-per-effect rule.

So the dependency surface of the current behaviour is: hypothetical
external callers only, plus the design question of whether silently
committing a null-valued write for an unnamed fact should be legal.
Nothing in-repo would need to change to make it refuse.
