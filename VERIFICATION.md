# Verification — stitch v0.1

Everything below was run for real on 2026-10-26 by Muse, on the rehomed
copy in this repo. The original draft's own claims were not trusted; where
they were wrong, that's recorded here and in the fixes list.

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
