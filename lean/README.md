# stitch, formally — a Lean 4 specification of the agreement and conservation core

This directory is a formal model of stitch, written in Lean 4 against
`SPEC.md` and the Python reference (`stitch-python/`). It does not
port the library; it states the two properties the whole design
rests on — **minting soundness** and the **conservation law** — as
machine-checked theorems about a model of the ledger, the audit, and
the write path.

Status, in one breath (details below, kept current as the work
happened): *(filled in at the end of the build, with the exact
`sorry` count and build output.)*

## Process log

**2026-10-29, Oracle lane (detached seat; Thor and ProArt excluded by
Casey's direction).** Before writing any Lean, the SPEC and the
Python reference were read end to end. The passages the model must be
faithful to, pinned down first:

- *Minting* (SPEC "Agreement (v0.2)" → "Minting"; `agreement.py:agree`):
  audit both ledgers (hash chain + conservation-law replay); refuse
  everything if either fails. Then find a SEND booking in either
  ledger; mint iff the *other* ledger holds a RECEIVE booking for the
  same exchange id whose `ns` is the send's `peer`, whose `peer` is
  the sender, and whose `ph` equals the send's `ph`. A booking is a
  `BIND` entry with `kind = "exchange"` whose payload is the
  descriptor `{peer, ph, side, xchg}` (`agreement.py:_bookings`).
- *Conservation* (SPEC "The conservation law"; `core.py:Tenant`,
  `stitches.py:effect`/`tick`): an EFFECT write computing surprise
  `s` is accepted iff `γ + s <= C` (plus a float epsilon in the code);
  on acceptance `γ := γ + s` and the entry records `s` and the new
  `g = γ`. TICK resets `γ := 0` and records `g = 0`. All other
  stitches leave γ unchanged and record the current `g`. The audit
  replays exactly this (`agreement.py:_audit`): γ accumulates each
  EFFECT's recorded `s` per tenant, resets on TICK, and every
  entry's recorded `g` must equal the replayed sum and stay within
  the budget in force at that entry's position.
- *Budgets* (SPEC "Tenant budget declarations (v0.3)"): a tenant may
  declare a budget in-ledger (`BIND` kind `"tenant"`, payload
  `{"budget": b}`, `b > 0`); the first declaration takes effect at
  its own position and governs from there on; undeclared tenants
  fall back to the audit's budget parameter (default `log₂3`).
  Write-time enforcement is a separate knob (`Tenant(budget=C)`);
  SPEC instructs setting both to the same C — the formal model makes
  that instruction a hypothesis of the writer theorem (below).

**Toolchain route.** `elan-init.lean-lang.org` does not resolve from
this box (curl exit 6), so elan 4.2.4 was installed from the
leanprover/elan GitHub release (aarch64 tarball) and the toolchain
from there — the documented fallback. The box has ~2.5 GB of free
disk, so **Mathlib is deliberately not used**: its prebuilt cache
alone exceeds the available space, and nothing in these two theorems
needs it. The model uses only Lean core (`Rat` for the budget
arithmetic, core `List` API).

## How the SPEC maps to the formal model
*(written alongside the sources; see the module docstrings for the
line-by-line mapping and the README section "Simplifications" for
every place the model is weaker than, or different from, the SPEC.)*

## Status (final, 2026-10-29)

**`lake build` is green. The `sorry` count is 0.** Both core
theorems are proved in full — including the conservation bridge
(`WTrace.lawful`), which connects the write rules to the audit's
replay. An axiom audit (`#print axioms`, run against the built
library) shows:

- `minted_sound` depends on **no axioms at all**.
- `WTrace.conservation`, `WTrace.lawful`, and their headline
  corollaries depend only on `propext` and `Quot.sound` — Lean's
  standard kernel axioms from the function/quotient infrastructure.
- The tamper-evidence lemma takes second-preimage resistance as an
  explicit *hypothesis argument*, so it too rests on no axiom
  beyond `propext`.

No `sorryAx` anywhere: nothing in this directory is a claim dressed
as a proof. What is *not* proved is listed under "Simplifications"
and "Not modelled" — those are scope boundaries, stated in the
open, not gaps hidden in the code.

Toolchain: **Lean 4.20.0** (aarch64), Lake 5.0.0. Build from this
directory with the toolchain on `PATH`:

```
export PATH=<lean-4.20.0>/bin:$PATH
lake build
```

or, on a machine with elan, `elan toolchain install
leanprover/lean4:v4.20.0` first (the version is pinned in
`lean-toolchain`). On this box elan's installer domain did not
resolve and the then-current stable (v4.35.0) did not fit the
remaining disk (its toolchain extracts to well over the ~2.4 GB
free), so 4.20.0 was stream-extracted from
`releases.lean-lang.org` into `~/stitch-lean/toolchain` — see the
process log. Real build output on completion:

```
✔ [5/7] Built StitchLean.Conservation
✔ [6/7] Built StitchLean
Build completed successfully.
```

and `grep -rn sorry StitchLean/` finds nothing (the word appears
only in prose explaining that there are none).

## The theorems, in plain words

**Minting soundness** (`Stitch.minted_sound`, Agree.lean). *If
exchange `x` mints between two ledgers, then the witnessing pair of
entries is really in the files:* one ledger contains a booking
entry recording a SEND of `x` by some tenant to a named peer; the
other contains a booking entry recording a RECEIVE of `x` by that
peer, naming the sender as its peer; and both entries commit to
the same payload hash. The `Agreement` record that `agree` derives
is therefore always witnessed by the ledgers themselves — minting
cannot manufacture agreement out of entries that are not there.
(Near-definitional, as expected: the value of the formalization is
that the Python mirror rule's exact conditions — cross-named
peers, equal `ph`, same `xchg`, complementary sides, audits first —
are now pinned in a form a machine checks, with the audit gates
part of the predicate rather than a comment about them.)

**Conservation** (`Stitch.WTrace.conservation`, Conservation.lean).
*Any sequence of accepted writes keeps every tenant's γ in
`[0, C]`* — equivalently, with η := C − γ, `γ + η ≤ C` per tenant
per tick, after every single entry, not just at the end of a tick.
The proof is parametric in the budget `C`: it never uses the value
log₂3, only the acceptance rule (an EFFECT write is admitted iff
`γ + s ≤ C` with `s ≥ 0`) and TICK's reset.

**The writer passes its own audit** (`Stitch.WTrace.lawful`,
headline form `Stitch.lawful_of_written`). *A ledger written by
accepted writes from the initial state is `Lawful`:* replaying it
the way a foreign auditor does, every entry's recorded `g` equals
the replayed γ, and every replayed γ is within the budget in force
at that entry's position. This is the property `agree` demands of
both ledgers before it will mint, proved of the write path itself.
It carries one hypothesis with a name, `DeclOK` (below).

**Tamper evidence at one position**
(`Stitch.chainHashesFrom_ne_of_head_ne`, Audit.lean). *Under the
stated second-preimage hypothesis on `H`, two ledgers whose next
entries differ cannot have equal hash chains from the same
previous hash.* This is the formal content behind the head-hash
anchor's `rewritten` verdict. It is the only result that spends
the hash hypothesis.

**`_bookings` bridge** (`Stitch.mem_bookings`, Model.lean). The
list the Python code computes and the membership predicate the
model reasons about select exactly the same bookings. A small
lemma, but it is what makes "the model follows SPEC" checkable
rather than asserted for the agreement half.

## How the SPEC maps to the formal model

- `Model.lean` — `Entry` (the fields the audit and agreement rules
  read; everything else erased), `Booking` (the exchange
  descriptor plus the booking entry's `ns`), `HasBooking` and
  `bookings` (the two forms of `agreement.py:_bookings`).
- `Audit.lean` — `chainHashesFrom`/`ChainValid` (the chain half of
  `_audit`, over an abstract `H`), `ReplayState`/`advance` (the law
  replay's state and step, in the auditor's exact order:
  position-aware first-declaration-wins budget update, then γ),
  `Lawful` (the replay verdict as an inductive predicate).
- `Agree.lean` — `Audited` (chain + law gates), `Mirror` (SPEC
  "Minting" step 4, field by field), `Minted` (both audits pass
  and a mirror exists, in either orientation), `minted_sound`.
- `Conservation.lean` — `WStep` (the accepted-write rules of
  `stitches.py`/`core.py`, sharing the auditor's `advance`),
  `WTrace` (a written ledger as a trace of accepted writes), the
  invariant and hygiene preservations, and the two theorems above.

## Simplifications — every place the model differs from the SPEC

Named, as agreed at the outset. None of these is silent in the
sources either; each is flagged where it lives.

1. **Integers, not floats, and no log₂3.** Amounts are `Int`. The
   budget is an arbitrary positive `C` throughout; log₂3 never
   appears. Why: on Lean 4.20's shipped libraries, `Rat` exists
   only as an internal `Std` type without a public ordered-field
   lemma library (that library lives in Mathlib, whose cache alone
   exceeds this box's free disk). What transfers: the conservation
   argument uses only linearly-ordered additive reasoning
   (admission test `γ + s ≤ C`, accumulation, reset), so the proof
   is the rational/real proof with the denominators cleared — the
   integer skeleton the task named as the fallback. What does not
   transfer: anything about the *value* log₂3, and float rounding
   (Python floats can drift below exact arithmetic in ways an
   integer model cannot express; SPEC's numeric-policy section is
   about the wire format, not the law, and is out of scope here).
2. **No epsilons.** The code checks `γ + s ≤ C + 1e-12` at write
   time (`_EPSILON`) and tolerates `1e-6`/`_EPS` slack in the
   audit's `g`-equality and budget checks. The model compares
   exactly, so it is *stricter than the code at the boundary*: a
   write admitted only by the epsilon is not a write in the model.
3. **Entries are abstracted.** Timestamps, cell ids, JSON payloads,
   and `h` values are erased. Stitches the law and the mirror rule
   treat identically (everything except EFFECT, TICK, exchange
   bookings, and budget declarations) collapse into `Entry.other`.
   A TICK's recorded `g` is 0 by construction in the model, as it
   is in the writer (`stitches.py` writes `"g": 0.0`
   unconditionally) — an auditor-side TICK entry claiming another
   `g` is a malformed input the Python audit rejects via the
   `g`-equality check; the model cannot represent it.
4. **The hash is abstract, and its hypothesis is idealized.** `H`
   is an uninterpreted function; second-preimage resistance is a
   theorem *premise* (an argument to the one lemma that needs it),
   not an axiom and not a claim about FNV-1a — which does not have
   the property, as SPEC itself states ("a tamper-evident canary,
   not a cryptographic hash"). Chain-level meaning is therefore
   conditional in exactly the way SPEC's own anchor section is.
5. **The writer shares the auditor's transition function.** In the
   model, a write's resulting state is `advance st e` by
   construction; in the code, writer and auditor are separate
   implementations of the same rules. The bridge theorem thus
   proves "the write discipline implies audit-cleanliness given
   the shared rules" — the same content, with implementation drift
   between two codebases explicitly out of scope (the Python test
   suite is what guards that in practice).
6. **`DeclOK`: the two-knobs rule is a hypothesis.** The bridge
   theorem assumes every in-force declaration declares the enforced
   budget `C`. Without it the theorem is false (a tenant declaring
   less than the writer enforces can be refused by the auditor for
   writes the writer accepted) — this is SPEC's own warning,
   formalized rather than smoothed over. The model's writer only
   ever declares `C`, and `DeclOK` is proved preserved by every
   step, so the hypothesis is discharged for any ledger the
   model's writer produces from a `DeclOK` state.

## Not modelled (scope, not gaps)

- **Signed bookings** (v0.4 `booking_keys` enforcement): `Minted`
  is the keyless semantics, which are also v0.4's whenever no keys
  are supplied. Signature verification would need an Ed25519 model
  and is a separate project.
- **The refusal taxonomy** (`no_send`, `peer_mismatch`, …):
  `Minted` is the positive predicate; which refusal Python returns
  (including its deterministic scan order) does not affect whether
  an exchange mints, and soundness is about minting.
- **N-party agreement** (`agree_n`): bilateral only, as tasked.
- **The anchor** (`verify_anchor` and its verdicts): the tamper
  lemma above is its one-position core; the prefix bookkeeping is
  not formalized.
- **The speculative pre-charge** (`Tenant.pending`,
  `pre_charge`/`commit_pending`): the model covers the committed
  write path the audit replays, with no pending charge outstanding
  — which is the state `effect()` itself runs in unless a caller
  deliberately uses the speculative API.
- **`WITHDRAW`'s shared pool**: SPEC defines it as a no-op for
  single-tenant deployments and a headroom release in a
  (future, v0.7) shared-budget pool. In the model it is an
  `Entry.other`: γ unchanged, as the replay treats it.
- **The surprise oracle**: `s` enters the model as the recorded
  value with its SPEC contract (`s ≥ 0` — a non-negative float of
  dissipated surprise). The KL computation itself is not verified;
  the audit in both code and model replays recorded `s`, never
  recomputes it.

## Process log (continued)

**Toolchain.** elan's installer script domain
(`elan-init.lean-lang.org`) does not resolve from this box. The
GitHub-releases route worked: elan 4.2.4's aarch64 tarball ships
`elan-init`, which installed elan and resolved `stable` to Lean
v4.35.0 — but the 4.35.0 toolchain's extraction ran the disk to
zero (`No space left on device` partway through `lib/lean`), on a
box with 2.5 GB free. The partial install was removed and Lean
**4.20.0** (356 MB `.tar.zst`, 1.7 GB extracted) was
stream-extracted — no intermediate tarball kept — into
`~/stitch-lean/toolchain`, and used directly via `PATH` (elan is
not in the build path). v4.20.0 was chosen as a modern release
whose extracted size fits with room for `.lake` artifacts; nothing
in the model needs anything newer. The 4.35 failure and this
choice are recorded here rather than in a commit message because
the next person on a small box will hit the same wall.

**The Rat detour.** First probes against 4.20 showed `Rat` is not
exported by `Init` or by `import Std` at all — the rational type
in 4.20's core split is `Std.Internal.Rat`, with instances but no
public lemma library. Rather than prove ordered-field lemmas from
`num`/`den` first principles, the model took the task's sanctioned
fallback — the integer skeleton (Simplification 1). The rest of
the environment shaped up well: core `List` lemmas,
`Option.getD`, and the `omega` tactic (which discharges the
model's linear integer arithmetic) are all in the shipped
libraries. Point updates are a local definition (`upd`, Audit.lean)
because `Function.update` is not exported under that name either.

**Proof engineering notes.** Two iterations are worth recording
for the next formalization lane. (1) In Lean 4.20, `cases` on an
inductive whose constructors carry implicit fields names only the
*explicit* fields in the case pattern; the implicits arrive as
inaccessible hypotheses and must be claimed with `rename_i` —
every `WStep` case split here uses that pattern. (2) The bridge
theorem's per-entry goals are discharged by computation
(`simp only` with the `advance` projection lemmas and `Entry.g` /
`Entry.ns`) instead of by `show`-ing the unfolded goal: the
unfolded form mentions the step's state variable, whose name the
induction does not preserve, while the computational proof does
not care what it is called.

**Verification.** `lake build` green from a clean `.lake`;
`sorry` count 0 by grep over the sources; `#print axioms` output
as quoted in "Status"; `git status` confirms no Python or Rust
file was touched — the only changes are this `lean/` directory,
on branch `stitch-lean`.
