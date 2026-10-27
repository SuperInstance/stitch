# Verification — stitch

Everything below was run for real, on the dates each section carries
(2026-10-26 and 2026-10-27), by Muse, on the copies in this repo.
The original draft's own claims were not trusted; where they were
wrong, that's recorded here and in the fixes list.

## v0.3 — adoption of the GLM draft, with fixes (2026-10-26)

The v0.3 draft was written by Casey's Z.ai assistant (GLM) on the
published v0.2, targeting the four known limitations named there (R1
non-ASCII pin, R2 budget declarations, R3 N-party agreement, R4 Ed25519
signatures). Muse assessed it independently before adopting anything:
the draft's suite passed as shipped (137/137 on this VM, counts exact),
its hand-rolled Ed25519 was proven RFC 8032-faithful against OpenSSL
before any claim was repeated, and three deficits were demonstrated
with running code (below). The draft is adopted with fixes; this section
records what ran, what was patched, and what is still open. GLM's own
v0.3 verification notes were not carried over: its R4 paragraph claimed
the copy-authenticity gap "closed" while nothing in the draft imported
the crypto module, and it cited a cross-check we could not find
evidence for. Where this record and the draft's disagree, this record
is the one we stand behind.

### What was wrong in the draft, and the fixes

1. **Crypto was an island.** `stitch/crypto` was implemented and tested
   but nothing imported it: `attest` still wrote the SHA-256 placeholder
   and the audit verified no signatures. **Fix (opt-in wiring):**
   `attest(..., secret_key=None)` writes a real Ed25519 signature over
   `"<tenant>:<cell>:<claim>"` when given a 32-byte key (default stays
   the placeholder, byte-identical); `audit_ledger(...,
   attest_keys=None)` verifies ATTEST signatures for keyed tenants and
   reports `attest_verified` / `attest_unsigned` / `attest_failures`
   plus a `signatures_ok` property. `AuditVerdict.ok` keeps its v0.2
   meaning (chain + law). Five wiring tests pin the behaviour,
   including tamper-after-signing (chain broken, failure recorded) and
   keyless audits being completely unchanged.
2. **Budget audit was position-blind.** Demonstrated against the
   draft: spend γ=2.0 in one tick (over the 1.5850 default), then
   `declare_budget(3.0)` afterwards — the draft's audit extracted
   declarations in a pre-pass and returned `law_ok` with max excess
   0.0. **Fix:** the audit judges each entry during its single replay
   pass against the budget in force at that entry's position; a first
   declaration takes effect at its own position and covers entries from
   there on. The attack now audits `law_ok=False` with max excess
   0.415, and a declaration made before a legitimate spend still covers
   it. Two regression tests pin both directions. `AuditVerdict.budgets`
   still reports the declared claims.
3. **Docstring claimed tests that didn't exist.** `ed25519.py` claimed
   RFC 8032 vectors were validated in its test file; the file had none.
   **Fix:** the claim was corrected, and the real RFC 8032 §7.1 vectors
   (TEST 1 and TEST 2, values read from the RFC text during adoption)
   were added as `tests/test_rfc8032_vectors.py` — public-key
   derivation and signatures match the RFC byte-for-byte, 6 tests.
   (During adoption, a widely-copied transcription of the TEST 2
   signature failed against this implementation while TEST 1 passed
   byte-for-byte; the RFC's own text was fetched and the pinned value
   corrected to the RFC's bytes — the tests now pin the RFC, not the
   transcription.)
4. **Version strings left at 0.2.0** in `pyproject.toml` and
   `Cargo.toml`. Bumped to 0.3.0 in both (`stitch/__init__.py` already
   carried 0.3.0; `Cargo.lock` updated by cargo).

### Test matrix — Python suite, 150 tests

150 = the draft's 137 (core 34, byte-exact 5, agreement 15, non-ASCII
41, budget 11, N-party 9, Ed25519 22) + 13 added on adoption (RFC 8032
vectors 6, position-aware budget 2, attest wiring 5). Suite runtime
~12–21 s per surface (the pure-Python Ed25519 dominates: ~270 ms per
sign/verify on this VM).

| Surface | Result |
|---|---|
| This VM (x86_64) | 150 passed; cross_lang PASSED; minimal_agent rc 0 |
| Oracle cloud VM (aarch64, Python 3.14.4) | 150 passed; cross_lang PASSED; minimal_agent rc 0 |
| Jetson Orin Nano (aarch64) | 150 passed; cross_lang PASSED; minimal_agent rc 0 |
| Jetson AGX Thor (aarch64, niced under another agent's load) | 150 passed; cross_lang PASSED; minimal_agent rc 0 |
| ProArt WSL (x86_64) | 150 passed; cross_lang PASSED; minimal_agent rc 0 |

**ProArt leg completed 2026-10-27** (the pending row above, closed):
run unchanged against commit `c84eabd` on eileen (x86_64 WSL2,
Python 3.14.4, pytest 9.1.1), reached via the Thor LAN hub
(`ssh proart-lan` from the Thor, 192.168.0.78:2206) because the direct
Oracle-slot route resets at the egress proxy while the box is healthy.
The run was executed live in a dedicated tmux session (`stitch`, window
`leg`) at Casey's request so he could watch from the desk, and teed to
`~/stitch-v03-proart.log` on the ProArt: 150 passed in 5.61 s,
cross_lang PASSED, minimal_agent rc 0. As a bonus beyond the matrix,
`cargo test` also ran there (cargo 1.99.1): 6/6 passed. The code
tarball was transferred VM → Thor → ProArt by scp at each hop with
sha256 verified identical on all three machines
(`6e82b5733a7f3fcbe4724ad225afd8232cd8daddc3fb8199261a509551aec00f`).
All five surfaces are now verified for v0.3.0.

Rust: `cargo test` 6/6 on this VM, crate byte-identical to v0.2 apart
from the version bump — the v0.3 features are Python-first and the docs
say so.

### Functional test — does it do what it is *for*? (2026-10-27)

The 150 tests above prove the code passes its own suite. Casey asked
the separate question: does stitch, used the way it is meant to be
used, actually do the job? A fresh scenario script — written for this
test, not part of the shipped suite — was run for real:
`stitch-python/examples/functional_scenario.py` (in this repo).

**Process.** Three physical selves, three machines: `proart`
(ProArt WSL, run live in tmux session `stitch`, window `func`, at
Casey's desk), `thor` (Jetson AGX Thor) and `vm` (this VM). Each self
ran `make <itself>` alone — no other machine contacted, no coordinator,
no shared clock — writing only its own ledger (proart 8 entries, thor
8, vm 6), including a non-ASCII payload (`Sitka — 世界`). The three
ledger files were then exchanged by plain scp (sha256 identical at
every hop: proart `9ed39332…`, thor `2461bd2a…`, vm `88881aa7…`) and
each machine independently ran `derive` over the same bundle. The
derive report contains no timestamps, paths or head hashes, so the
three reports must be byte-identical if independent derivation really
agrees — and they were: all three files md5
`f75ffde7c3f0b3a73a2f9ed16a7436fe`, diff-clean.

**What the scenario checked, and what happened:**

- **Sovereign audit:** each genuine ledger audits chain OK + law OK
  on every machine.
- **Mint only on agreement:** `x-agree` (proart books SEND, thor books
  the mirror RECEIVE, same payload — non-ASCII included) **mints**,
  payload hash `01514383…`; `x-dispute` (receiver booked rev 4 against
  the sender's rev 3) **refuses** with `payload_mismatch`. Nothing
  partially minted.
- **N-party:** the proart→thor→vm→proart ring **mints** under
  `agree_n` (parties `[proart, thor, vm]`, ph `528b57d6…`).
- **Truncation:** the agreement-time anchor reports the genuine
  ledger `anchored` and a one-line-shorter copy `truncated`.
- **Two failure modes, two different checks:** a forged ledger
  (valid chain, γ=2.5 in one tick) passes the chain and fails the
  law; a tampered copy of proart's ledger (one character changed)
  fails the chain at entry 1 and passes the law. `verify()` alone
  would have waved the forged one through — the audit is the judge,
  as documented.
- **Conservation at write time:** a first 1-bit `effect` commits; a
  second 1-bit `effect` in the same tick is rejected, appends
  **nothing** (entries 2 → 2), and does not mutate the cell.
- **Budgets, position-aware:** the retroactive-declaration attack
  (spend γ=2.0, *then* declare 3.0) still audits law-violated; the
  same spend with the declaration first audits clean.
- **Signed attest, opt-in:** proart's ATTEST, signed with a fixed
  *published test key*, verifies under the right public key
  (verified=1, signatures_ok) and fails under a wrong key
  (verified=0, 1 failure); with no keys supplied the audit does no
  signature work at all, exactly as in v0.2.

**Two behaviours worth naming because they surprise:**
`agreed_state` (bilateral fold of proart+thor) also mints `ring-1` —
correctly, because the ring's proart→thor leg *is* a true bilateral
mirror pair. And `agreed_state_n` refuses `x-agree`/`x-dispute` with
`missing_mirror` — also correctly: an exchange one of the N parties
never booked is not N-party shared state, however clean the bilateral
pair is.

**The one want it does NOT meet, probed on purpose:** a fresh ledger
that merely *claims* the identity `thor` and mirrors the `x-agree`
booking **mints against proart's genuine ledger**. Bookings are
unsigned and identity is a string claim, so agreement proves two
*records* agree, not that a copy is authentic — the documented
key↔identity gap, now demonstrated inside a functional scenario
rather than only stated. Signed attestations narrow this for ATTEST
entries only; closing it for bookings is future work, not a v0.3
claim.

Verdict: for the job it is sold for — sovereign, asynchronous,
accountable records that mint shared state only where they agree —
**yes, it does it, on real machines, with identical results derived
independently.** For authenticated shared state, not yet, and this
record says so in the same breath.

### Cross-instance exercises (v0.3 code, four selves)

Both exercises re-run with each self on its own machine, ledgers
exchanged as files, exactly as in v0.1/v0.2. Because the ProArt was
off, the v0.2 exercise's pairs were oracle↔orin and vm↔thor (the v0.2
run paired proart↔thor); the VM was a full self this time rather than
the witness.

- **v0.1 exercise:** bundle = 4 genuine ledgers + forged + tampered.
  All four machines' audits agree verdict-for-verdict (auditor name in
  the header aside): forged → chain OK, **law VIOLATED** (γ=2.5 >
  1.5850); tampered → **chain BROKEN**, law OK; all genuine OK.
- **v0.2 exercise:** both pairs **MINT** x-agree (identical payload
  hash `e435cb0a72de` on both pairs); both pairs **REFUSE** x-dispute
  (`payload_mismatch`); every cross-pair combination refuses with the
  correct reason (`peer_mismatch`, `missing_mirror`, `no_send`). All
  four machines' derive outputs are byte-identical (md5
  `4d399e146040344c952259510880c441`).

### Compatibility with v0.2, measured

- ASCII artefacts are fully bidirectional: a v0.2-written ASCII ledger
  audits clean under v0.3, vice versa, and cross-version ASCII
  exchanges still mint.
- Non-ASCII is a hard break in both directions, as documented in the
  assessment: v0.3 rejects v0.2-era non-ASCII ledgers (chain broken at
  the first non-ASCII entry) and v0.2 rejects v0.3's; ASCII payload
  hashes are identical across versions (`bfa9434c…`), non-ASCII differ
  (v0.2 `3cd43ebe…` vs v0.3 `e5950e9a…`). The v0.2 artefacts were
  already nonconformant against the Rust implementation; the pin makes
  Python match Rust, not the other way around.

### Still open after v0.3 (stated as plainly as the draft's gaps were)

- **Key↔identity binding is out of band.** Signatures prove a key
  signed an attestation; nothing yet proves the key belongs to the
  tenant identity it claims. Copy authenticity is enforced only where
  writers opt in and auditors supply keys — narrowed, not closed.
- Only ATTEST entries can be signed; agreement bookings are unsigned.
- `verify` does not screen for small-order A/R points. The cofactored
  check still rejects signatures failing the verification equation,
  and all RFC/OpenSSL probes passed, but a strict verifier would add
  the screening.
- The Ed25519 module is slow (~270 ms/op on a small VM). Fine at
  ledger scale; not a bulk-signing tool.
- Budgets remain per-tenant and self-declared: a tenant can declare a
  huge budget. Declarations make budgets *auditable*, not *honest* —
  the same shape as surprise inputs (see v0.1 limitations, unchanged).
- The Rust audit does not yet know about declarations, N-party
  agreement, or signatures; a v0.2-semantics audit of a v0.3 ledger
  with a declared budget judges against the assumed budget.

## v0.2 — the agreement layer (2026-10-26)

v0.1's verification ended with a named gap: stitch verified but did not
agree — no operation let two sovereign ledgers mint shared state, and the
cross-instance audit was ~60 lines of glue in an example, not library
API. v0.2 closes that gap, and this section is its verification record.

**What v0.2 adds** (SPEC.md, "Agreement (v0.2)"):

- Exchange bookings with the existing `BIND` stitch (kind `"exchange"`,
  payload = `{xchg, side, peer, ph}`) — no new stitch, no new entry
  fields; the count stays at 11. Helpers: `book_send` / `book_receive`.
- `audit_ledger` — the v0.1 exercise's audit glue, productised: chain
  integrity (with first-broken index), conservation-law replay (with
  maximum excess), head hash, entry count, tenant, assumed budget.
- `agree` / `agreed_state` — bilateral minting. An exchange is shared
  state only when both ledgers pass audit AND the mirror bookings agree
  exactly (exchange id, payload hash, counterpart identities). Refusals
  are distinct verdicts: `no_send`, `missing_mirror`, `peer_mismatch`,
  `payload_mismatch`, `chain_broken`, `law_violated`. Nothing partially
  mints.
- `verify_anchor` — the head-hash pin. An Agreement records both
  ledgers' head hash + entry count at agreement time; re-audit against
  the pin reports `anchored` / `truncated` / `rewritten` /
  `chain_broken` / `missing`. This closes the tail-truncation blindness
  recorded in the v0.1 limitations — for exchanges agreed before the
  cut, and only relative to a pin the holder kept (see SPEC).
- The audit (not minting) is also ported to Rust.

**Test suite: 54 tests** (39 from v0.1 + 15 new in
`tests/test_agreement.py`, covering: canonical payload hash; booking
wire format; audit of clean / forged-law / tampered ledgers; clean
mint; `no_send` vs `missing_mirror`; payload mismatch; peer mismatch;
forged-law and tampered rejection inside `agree`; tail-truncation
caught by the anchor, including the honest case that a fresh fold of
the truncated pair still mints under a *different* head — the pin is
what catches it; anchor holding under append-only growth; rewrite and
missing-file detection).

| Surface | Machine / arch | Python | pytest | cross_lang.py | Demo |
|---|---|---|---|---|---|
| This VM | x86_64, Ubuntu, kernel 6.8 (hatch VM) | 3.12.3 | **54/54 pass** | pass | pass |
| Oracle Cloud | aarch64, OCI Ampere A1 | 3.14.4 | **54/54 pass** | pass | pass |
| Orin | Jetson Orin Nano Super, aarch64 tegra | 3.12.3 | **54/54 pass** | pass | pass |
| Thor | Jetson AGX Thor, aarch64 tegra | 3.12.3 | **54/54 pass** | pass | pass |
| ProArt | x86_64 WSL2 (eileen), RTX 4050 | 3.14.4 | **54/54 pass** | pass | pass |

- **Thor was load-gated again.** Observed before running: load average
  6.33 / 5.80 / 6.50 on 14 cores (~45%), 78 GiB RAM available, the other
  agent's workload visibly active (a Foundry Blender mint). Not
  overloaded, so the suite and exercise ran niced from /tmp, and all
  scratch was removed afterwards.
- **Rust: 6/6 on this VM** (the 4 v0.1 tests + 2 new audit tests: clean
  audit with head-hash match; tamper → `chain_broken_at = 1`; forged
  s=2.5 ledger → chain ok, law violated, excess = 2.5 − log₂3).
  **Minting is NOT ported to Rust — v0.2 agreement is Python-first, and
  this file says so rather than implying parity.** The Rust audit
  inherits the crate's ASCII-domain caveat (re-serialisation of
  non-ASCII content differs from Python's, as in v0.1).

**Cross-instance exercise v0.2** (`stitch-python/examples/cross_instance2.py`).
Five machines each ran stitch as a distinct sovereign self — `vm`
(witness, books nothing), `oracle`→`orin` and `proart`→`thor` as booked
pairs. Each pair booked two exchanges: `x-agree` (both sides the same
payload) and `x-dispute` (the receiver booked a different payload than
the sender sent). Ledgers were exchanged as plain files. Every machine
then folded the full bundle with `agreed_state` — library API this
time, not example glue. **All five derive outputs were byte-identical**
(md5 `78b8f78f592da8a3f5fd0fedde6e68b9`), 20 lines:

```
MINT   oracle.jsonl+orin.jsonl x-agree oracle->orin ph=e435cb0a72de
REFUSE oracle.jsonl+orin.jsonl x-dispute payload_mismatch
REFUSE oracle.jsonl+proart.jsonl x-agree missing_mirror
REFUSE oracle.jsonl+proart.jsonl x-dispute missing_mirror
REFUSE oracle.jsonl+thor.jsonl x-agree peer_mismatch
REFUSE oracle.jsonl+thor.jsonl x-dispute peer_mismatch
REFUSE oracle.jsonl+vm.jsonl x-agree missing_mirror
REFUSE oracle.jsonl+vm.jsonl x-dispute missing_mirror
REFUSE orin.jsonl+proart.jsonl x-agree peer_mismatch
REFUSE orin.jsonl+proart.jsonl x-dispute peer_mismatch
REFUSE orin.jsonl+thor.jsonl x-agree no_send
REFUSE orin.jsonl+thor.jsonl x-dispute no_send
REFUSE orin.jsonl+vm.jsonl x-agree no_send
REFUSE orin.jsonl+vm.jsonl x-dispute no_send
MINT   proart.jsonl+thor.jsonl x-agree proart->thor ph=e435cb0a72de
REFUSE proart.jsonl+thor.jsonl x-dispute payload_mismatch
REFUSE proart.jsonl+vm.jsonl x-agree missing_mirror
REFUSE proart.jsonl+vm.jsonl x-dispute missing_mirror
REFUSE thor.jsonl+vm.jsonl x-agree no_send
REFUSE thor.jsonl+vm.jsonl x-dispute no_send
```

What this proves: the two genuinely-mirrored exchanges minted on every
machine, including the witness that took no part in them; the two
disputed exchanges minted nowhere; and every non-pair fold refused with
the correct distinct reason. Five physical selves, files alone, no
coordinator, no shared clock — identical shared state, derived
independently. The v0.1 claim (verification works asynchronously) now
extends to agreement itself.

**What v0.2 still does not solve** (stated, not smoothed over):

- **Copy authenticity.** Identity is still a string claim; there are no
  signatures (Ed25519 deferred — not in the Python stdlib, and the
  zero-dependency rule outranks it). Agreement proves two *records*
  agree; a fabricated copy of someone's ledger would "agree" with
  itself. Hold copies you trust, or witness head hashes out-of-band.
- **Agreement ≠ performance.** A minted exchange proves both sides
  booked the same payload hash. It does not prove the payload moved,
  was read, or corresponds to anything real.
- **Budgets still are not recorded in ledgers.** The audit carries its
  assumed budget in the verdict, but a custom-budget tenant still
  cannot prove its budget from its ledger alone.
- **The anchor trusts the pin.** Truncation/rewrite detection is
  relative to an Agreement record the holder kept intact; FNV-1a is a
  canary, not a cryptographic hash.
- **Bilateral only.** Minting is defined for two ledgers. N-party
  agreement is pairwise folds, by design — no group primitive was
  added.
- **Non-ASCII encoding pin still owed** (see SPEC byte-exactness).

---

# v0.1 record (2026-10-26)

## Test matrix — Python suite (39 tests), demo, cross-language harness

The suite is run from inside `stitch-python/` (`python -m pytest tests/`),
the documented invocation; the package is not installed site-wide.

| Surface | Machine / arch | Python | pytest | cross_lang.py | Demo |
|---|---|---|---|---|---|
| This VM | x86_64, Ubuntu, kernel 6.8 (hatch VM) | 3.12.3 | **39/39 pass** | pass | pass |
| Oracle Cloud | aarch64, OCI Ampere A1, kernel 7.0.0-1013-oracle | 3.14.4 | **39/39 pass** | pass | pass |
| Orin | Jetson Orin Nano Super, aarch64, kernel 6.8.12-1021-tegra | 3.12.3 | **39/39 pass** | pass | pass |
| Thor | Jetson AGX Thor, aarch64, kernel 6.8.12-1021-tegra | 3.12.3 | **39/39 pass** | pass | pass |
| ProArt | x86_64 WSL2 (eileen), RTX 4050 | 3.14.4 | **39/39 pass** | pass | pass |

Notes:

- **ProArt was run later the same day.** During the first session its reverse
  tunnel (Oracle loopback port 2222) was down after a reboot — WSL had not
  brought the tunnel service back. Once Casey restarted WSL and the tunnel
  returned, the full suite ran there too: 39/39 pass, cross-language harness
  pass, demo pass (Python 3.14.4, pytest 9.1.1, x86_64 WSL2 on eileen).
  Scratch was removed from the box afterwards.
- **Thor was load-gated, per Casey's instruction.** Observed before running:
  load average 6.38 / 6.62 / 7.42 on 14 cores (~45%), 78 GiB RAM available,
  the resident workload being a Blender render from another agent's lane.
  Not overloaded, so the full (sub-second) suite ran, niced, from /tmp,
  and all scratch was removed afterwards.
- Demo behaviour is identical on every surface: 12 ledger entries, hash
  chain valid, the two over-budget writes rejected (γ would reach 2.0 >
  log₂3 ≈ 1.585).
- Invocation wart: running `pytest stitch-python/tests/` from the repo root
  fails collection (`No module named 'stitch'`) because the package is not
  installed and the root is not on `sys.path`. Run from `stitch-python/`, or
  `pip install -e stitch-python` first.

## Rust

No fleet machine had a Rust toolchain (`cargo`/`rustc` absent on the VM,
Oracle, Orin, and Thor). rustup (stable, rustc 1.99.1) was installed on this
VM during verification solely to test the crate.

**As shipped, the Rust crate could not compile — for two independent
reasons:**

1. `serde_json` and `sha2` were declared under `[dev-dependencies]` while
   `src/lib.rs` uses both in library code.
2. `Ledger::append` held a mutable borrow of the entry across an immutable
   serialize of the same value — borrow-checker error E0502.

After fixing both (dependencies moved to `[dependencies]`; the borrow
scoped), the crate compiles clean and:

```
running 4 tests
test tests::test_cross_lang_byte_exact_with_python ... ok
test tests::test_fnv1a_known_vectors ... ok
test tests::test_tenant_budget ... ok
test tests::test_ledger_append_and_verify ... ok
test result: ok. 4 passed; 0 failed
```

`test_cross_lang_byte_exact_with_python` pins reference hashes produced by
the *Python* implementation (`b513cabe780e2894`, `24c56ec21ef20816`) and the
exact expected file bytes — so Python↔Rust byte-exactness is now verified
from both sides, for ASCII content (see limitations).

## Cross-instance exercise (the async-scalability claim, tested)

`stitch-python/examples/cross_instance.py`. Four machines each ran stitch
as a **distinct sovereign self** — tenant identities `vm`, `oracle`,
`orin`, `thor` — each writing its own 4-entry ledger (BIND, EFFECT,
ATTEST, TICK; γ spent 1.0 of 1.585) on its own disk, contacting nobody.
The ledger files were then exchanged by plain `scp` file copy — no server,
no coordinator, no shared clock (first-entry timestamps across the selves
spread over ~24 s of unsynchronised local clocks; the audit never looks at
them). Two adversarial files were added to the bundle:

- `forged.jsonl` — written with raw `Ledger.append` calls claiming an
  EFFECT of s = 2.5 bits in a single tick. Hash chain perfectly valid;
  conservation law broken.
- `tampered.jsonl` — a copy of Orin's ledger with one recorded value
  altered (`"alive"` → `"a1ive"`), hashes untouched.

Each of the four machines then audited the full bundle **independently,
from its own copy**. All four auditors returned byte-identical verdicts:

```
audit by <self>: assuming budget C=log2(3)=1.5850 for all tenants (v0.1 ledgers do not record per-tenant budgets); timestamps ignored
  forged.jsonl:   entries=2 chain=OK     law=VIOLATED: gamma=2.5000 exceeds budget 1.5850 (tenant mallory, op EFFECT)
  oracle.jsonl:   entries=4 chain=OK     law=OK
  orin.jsonl:     entries=4 chain=OK     law=OK
  tampered.jsonl: entries=4 chain=BROKEN law=OK
  thor.jsonl:     entries=4 chain=OK     law=OK
  vm.jsonl:       entries=4 chain=OK     law=OK
```

What this proves:

- Independent verification works with nothing but the file. Chain integrity
  and law compliance are **separate** checks, and each failure mode was
  caught by exactly the check designed for it: tampering by the chain,
  law-breaking by the replay audit — the forged ledger's chain is valid,
  so `verify()` alone would have waved it through.
- No synchrony anywhere: no sequencer, no coordinator, no clock agreement,
  no round-trips. Selves that have never met reach identical verdicts
  whenever they get around to looking. That is the
  simplicity→asynchronous-scalability claim, holding in practice.

What it exposes (the honest gap):

- **None of this interop is in the library.** stitch v0.1 has no import,
  export, or cross-ledger operation at all, and no operation that records
  *agreement* between two ledgers. The audit above is ~60 lines of new glue
  in the example, built only from the file format + `Ledger.verify()`.
  `attest`/`contest` take `Cell` objects from the caller's own ledger, so
  referring to a foreign self's cell means hand-reconstructing that cell
  from their ledger first — possible, but unimplemented and untested.
  Sovereign records: done. Independent verification: works, with glue.
  Agreement minting shared state: **not implemented** — today it would
  happen out-of-band, between the humans/agents holding the files.

## Fixes applied during rehoming

Python:

1. `_cell_id` rendered an absent parent as the string `"None"`;
   SPEC mandates empty string (and Rust already did it that way) — Python
   and Rust cell IDs would have diverged on every parentless bind. Fixed.
2. `_now_ns` computed nanoseconds via `int(time.time() * 1e9)`, losing
   precision to float rounding. Now `time.time_ns()`.
3. `Ledger` docstring (and the README table) described the hash as
   "content XOR previous hash"; the implementation and SPEC seed FNV-1a
   *with* the previous hash. Docs corrected.

Rust:

4. `Cargo.toml`: `serde_json`/`sha2` moved from `[dev-dependencies]` to
   `[dependencies]` (the crate could not compile as shipped).
5. `src/lib.rs`: `Ledger::append` borrow error (E0502) fixed by scoping
   the mutable borrow; unused import removed; docstrings corrected (the
   crate implements `bind` only, not "bind + effect" as claimed).

Tests / docs:

6. `tests/cross_lang.py` as shipped looked for a `stitch_test` binary that
   does not exist and silently skipped — the "cross-language check" was
   theatre. Rewritten: it now pins real Python ledger reference vectors,
   and a matching Rust test asserts the identical bytes.
7. SPEC: changelog date corrected (v0.1 was dated 2026-10-27; it was sent
   2026-10-26); "No global clock, no coordinator" and the non-ASCII
   limitation written into the byte-exactness section.
8. README: the simplicity→async-scalability principle stated as a design
   section; status corrected; provenance added.

## Known limitations (v0.1, all verified or by direct inspection)

- **Non-ASCII divergence.** Python's `json.dumps` escapes non-ASCII by
  default; Rust's `serde_json` emits raw UTF-8. Entries containing
  non-ASCII text therefore hash differently across implementations.
  Byte-exactness is verified for ASCII only. Pinning the encoding is a
  spec decision (v0.2).
- **Conservation is enforced at write time only by the stitch layer.**
  `Ledger.append` is a raw writer: any code that bypasses the stitch
  functions can record law-violating entries under a valid hash chain
  (demonstrated by `forged.jsonl`). Verifiers must run the law audit —
  replay `s` per tenant per tick — not just `verify()`.
- **Budgets are not recorded in the ledger.** An auditor must assume each
  tenant's configured budget (default log₂3). A tenant running a custom
  budget cannot prove it from its ledger alone.
- **Surprise inputs are self-reported.** The KL arithmetic is exact, but
  the predicted/observed distributions fed to it come from the writer.
  The law bounds *claimed* surprise; it cannot audit honesty of inputs.
- **Tail truncation is undetectable from the file alone.** Deleting
  trailing entries leaves a shorter, still-valid chain. Detecting it
  needs an external anchor (a witnessed hash/length) — which is itself
  an asynchronous, out-of-band step, by design.
- **One writer per ledger file.** The append lock is in-process
  (threading lock / mutex). Two processes appending to the same file can
  interleave and corrupt the chain. This matches the sovereign-self
  model — each self owns its file — but it is a real constraint.

## Assessment

The Python implementation is sound and behaves exactly as specified on
every surface it ran on. The draft's prose oversold the Rust side (a
skeleton that had never compiled) and its cross-language test (a skip
dressed as a check); both are now real and verified. The core claim —
that a primitive this simple needs no synchrony, no coordinator, and no
shared clock to scale asynchronously — survived contact with the
implementation: four independent machines reached identical verdicts
from files alone. The missing half is agreement: stitch records each
side's truth impeccably and verifies the other's, but the step where two
sides mint shared state is still outside the library, waiting to be
designed as deliberately as the rest.

## Cold test & documentation overhaul (2026-10-27)

**Method.** The repo was tested from the outside, as a new arrival
would meet it: a fresh public clone at `1dbcf0b`, a clean venv, and
every documented command run literally. Two independent tracks: (1) a
cold agent given only the repo's markdown docs, tasked with learning
the concepts, running the suite and every example, answering four
questions from docs alone, and writing a working two-agent program
(it did — first run, mint on mirror and `payload_mismatch` on dispute,
built almost entirely from README's Agreement section); (2) a literal
run of every README instruction, code block and install route.
Caveat, recorded for honesty: the cold agent ran as a subagent with
inherited conversation context, so its coldness was imperfect; the
literal track had no such contamination, and the two tracks' findings
agreed on every blocker.

**Findings — 15 friction items (cold agent) + 13 (literal track),
deduplicated to the load-bearing set:**

- **B1 (blocker): `pip install` was entirely broken.** `pyproject.toml`
  referenced `readme = "../README.md"` and a `LICENSE` file outside the
  project root; setuptools refused to build, plain and `-e`. This
  file's v0.1 section even recommended the broken `-e` route.
- **B2 (blocker): the PyPI route doesn't exist.** No distribution named
  `stitch-substrate` on PyPI (checked 2026-10-27); README's "(when
  published)" hedge gave no way to tell.
- **M1: SPEC's `attest` signature was printed with cell and claim
  swapped**; following it positionally crashed
  (`AttributeError: 'str' object has no attribute 'id'`).
- **M2: the two-budget trap.** `declare_budget` records a budget for
  auditors but does not change write-time enforcement; the enforcing
  knob (`Tenant(budget=…)`) appeared in no markdown doc. Verified:
  after declaring 3.0, a tenant's budget stays 1.5850 and a 2-bit tick
  is still rejected.
- **M3: test setup undocumented.** Clean env → `No module named pytest`;
  suite run from the repo root failed collection (9 errors); commands
  assumed a `python` binary.
- **M4: quiet specification rot.** SPEC's payload-hash formula omitted
  `ensure_ascii=False` (diverges for non-ASCII for any re-implementer;
  the library itself was correct); SPEC's Tenant-identity section was
  stale v0.2 text contradicted later in the same file; three documents
  carried three different roadmaps; SPEC's N-party section listed
  reason *constant names* where runtime values are lowercase strings;
  README's stitch table used the keyword `to` as a parameter name
  (actual: `to_identity`); SPEC's crypto examples never said messages
  must be `bytes`; verdict-object fields (notably `.reason`) were
  never listed in one place; ledger lifecycle (flush-per-append,
  `close()`, one-writer-per-file) appeared in no user-facing doc;
  examples other than `minimal_agent.py` had no documented invocation;
  `functional_scenario.py --help` exited 1; this file's banner dated
  everything 2026-10-26 while containing 2026-10-27 sections.
- **What carried the cold agent** (kept intact): README's Agreement
  section as a complete correct recipe, the documented test command's
  claimed count of 150, SPEC's budget/N-party/Ed25519 sections'
  semantics, and limitations stated in the docs rather than discovered
  in code — the cold agent learned all ten honest boundaries from the
  docs alone.

**Fixes applied in this pass:** `pyproject.toml` readme inlined and
license fixed (`pip install` and `pip install -e` both verified in a
clean venv; a `test` extra declares pytest); `tests/conftest.py` puts
the package on `sys.path` so the suite runs from the repo root as well
(150/150 both ways); `functional_scenario.py` grew a real `--help`
(exit 0); SPEC corrected (attest order, payload-hash formula, tenant
identity, N-party reasons, a verdict-fields table, the two-knobs
paragraph, ledger close in the budget example); README rewritten where
it misled (installation with honest PyPI status, quickstart budget and
lifecycle notes, examples table, tests with pytest setup,
`to_identity`); ARCHITECTURE's roadmap retired to a pointer at SPEC's
changelog; the stale `Tenant` docstring in `core.py` updated. And the
educational layer the cold repo lacked entirely: **AGENTS.md** (map,
reading order, run-everything commands, the five traps, invariants,
never-overclaim list) and **tutorials/** — three complete templates
for real jobs (accountable journal; agent-to-agent handoff with
derived receipt; corroborated observers), each executed end-to-end to
produce the outputs quoted in its README, including the refusal and
tamper paths.

**Still open after this pass:** the package remains unpublished on
PyPI (a publication decision, not a code gap); the Rust crate's audit
still judges v0.3 ledgers against the assumed budget (Python-first,
documented); no CI yet runs the install route in a clean venv — the
cold agent suggested one, and it is the right next hardening step.
