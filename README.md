# stitch

A tiny, language-portable substrate primitive for accountable agent state.

`stitch` is the smallest binding operation in a quilt. This library provides stitches: 11 operations on cells of agent state, each one append-only, conservation-law-enforced, and byte-exact across languages.

> The smallest thing that, if missing, makes every layer above it weaker. Not an LLM, not a runtime, not a framework. A primitive.

---

## The design principle: simplicity is what makes it scale

`stitch` is deliberately small, and the smallness is not an aesthetic choice — it is the scaling strategy. Every operation is a **local, append-only write to your own ledger**. Nothing in the write path waits on anyone else:

- **No central sequencer or coordinator.** A tenant writes to its own ledger file. There is no server to ask, no leader to elect, no lock to take.
- **No global clock.** The `t` field is the writer's local observation of time. The only ordering that matters is the order of entries inside one ledger, and that is carried by the hash chain itself — not by comparing timestamps across machines.
- **No synchronous agreement.** Each side keeps its own sovereign record. Verification (replaying a hash chain) and agreement (two sides comparing what they each recorded) happen later, whenever the records meet — minutes or months after the writes. In v0.2 that agreement step is library API (`agree`, `agreed_state`), but it is still just a fold over two files: nothing about it requires the sides to be online together, or ever to have been.
- **Minimal surface, minimal dependencies.** 11 operations, one file format, one hash function. The Python implementation is stdlib-only. Anything that would make a write wait — consensus, routing, scheduling, a runtime — lives in a layer above, or nowhere.

Because a write never blocks on a network, a peer, or a clock, writers scale by simply not coordinating: a thousand independent ledgers are a thousand independent appends. Coordination is deferred to read/verify time, where it can be batched, cached, or skipped — and where being slow is merely slow, never a stall in someone else's write path.

---

## What problem does it solve?

Most agent systems treat state as a side-effect. LLM agents write to memory streams, tool calls mutate Python dicts, eval results get cached in JSON files — and none of it is accountable. There is no way to ask "how much surprise did this agent dissipate this tick?", no way to enforce "this tenant has used X bits of surprise budget", no way to replay state from a tamper-evident log.

`stitch` makes agent state accountable. Every write is an `effect` stitch that computes the KL divergence between what the agent predicted and what it observed, charges that against the tenant's γ budget, and either commits the write or rejects it for violating `γ + η ≤ log₂3`. Every operation is appended to a hash-chained JSONL ledger. Every tenant has a per-tick budget. Every cell can be attested, delegated, contested, merged, revoked, or withdrawn.

The conservation law `γ + η ≤ log₂3` is the Shannon information content of one trit (≈1.585 bits). It is a *design axiom*, not a derived physical truth — it bounds how much accountable state one tenant can produce per tick. Treat it as a quota, not a physics equation.

---

## The 11 stitches

**Data plane (5):**

| Stitch | What it does | γ cost |
|---|---|---|
| `bind(tenant, ledger, kind, payload, parent_hash=None)` | Create a new cell | 0 (free) |
| `link(tenant, ledger, cell_a, cell_b, relation)` | Declare a relation between two cells | 0 |
| `effect(tenant, ledger, surprise, cell, key, predicted, observed)` | Write to a cell | γ += surprise(predicted, observed) |
| `view(cell, key=None)` | Read from a cell (free, not logged) | 0 |
| `tick(tenant, ledger)` | Advance time, reset γ to 0 | resets γ |

**Governance plane (6):**

| Stitch | What it does |
|---|---|
| `attest(tenant, ledger, cell, claim)` | Sign a claim about a cell |
| `delegate(tenant, ledger, cell, to, scope)` | Assign partial agency |
| `contest(tenant, ledger, cell, reason)` | Challenge a cell's validity |
| `merger(tenant, ledger, cell_a, cell_b, kind, payload)` | Combine two cells into a new one |
| `revoke(tenant, ledger, cell, reason)` | Undo a cell's authority (append-only, no deletion) |
| `withdraw(tenant, ledger, amount=None)` | Release budget back to the pool |

---

## The conservation law

For each tenant, each tick:

```
γ (surprise dissipated) + η (headroom remaining) ≤ C = log₂3 ≈ 1.585 bits
```

- **γ** is the cumulative surprise the tenant has produced this tick (KL divergence between predictions and observations, summed across all `effect` stitches).
- **η** is the headroom remaining before the tenant hits the ceiling.
- **C** is the per-tick budget. The default is `log₂3` (one trit). Configurable per-tenant.

When a tenant's γ + a new write's surprise would exceed C, the `effect` is **rejected** — it returns `False`, no ledger entry is appended, the cell's payload is not mutated. The caller must either `tick` (reset γ) or accept that the tenant is out of budget for this tick.

This is *not* a thermodynamic law. It is a design invariant that makes agent state budgetable. The number `log₂3` was chosen because one trit is the smallest unit of accountability: you either knew the answer (γ=0), you didn't know but had a fair guess (γ≤1), or you were surprised (γ>1). One trit per tick per tenant is tight but workable.

---

## Agreement: two ledgers mint shared state (v0.2)

A single ledger is one self's truth. Shared state exists only where two selves' records **agree** — and v0.2 makes that a library operation instead of an out-of-band judgement.

An exchange is booked by each side into its *own* ledger, using the existing `bind` stitch (no new opcode — the count stays 11):

```python
import stitch

# Alice's side, in Alice's ledger:
stitch.book_send(alice, alice_ledger, xchg="invoice-7", peer="bob",
                 payload={"doc": "harbor-chart", "rev": 3})
# Bob's side, in Bob's ledger, whenever it happens:
stitch.book_receive(bob, bob_ledger, xchg="invoice-7", peer="alice",
                    payload={"doc": "harbor-chart", "rev": 3})
```

A booking records the exchange id, the counterpart, and the SHA-256 of the payload — not the payload itself. Later, when the two files meet (any transport, any delay), either side — or a third party holding both copies — derives the verdict:

```python
verdict = stitch.agree("alice.jsonl", "bob.jsonl", "invoice-7")
verdict.agreed        # True — the mirror bookings match, both ledgers audit clean
verdict.payload_hash  # the hash both sides committed to
verdict.head_a        # Alice's head hash, pinned at agreement time

# Whole fold at once: every exchange booked in either ledger,
# partitioned into what minted and what was refused (and why).
minted, refused = stitch.agreed_state("alice.jsonl", "bob.jsonl")
```

The rules are strict, and every refusal is distinct: no send booked (`no_send`), the mirror never booked (`missing_mirror`), the receive names different parties (`peer_mismatch`), the two sides recorded different payloads under one id (`payload_mismatch`), or either ledger fails audit (`chain_broken`, `law_violated`). Nothing ever partially mints.

The Agreement also pins both ledgers' head hashes. If a ledger's tail is later truncated, `stitch.verify_anchor(path, verdict.head_a, verdict.entries_a)` reports `truncated` — closing the v0.1 blind spot where a shortened ledger still verified clean. Agreement proves the two *records* agree; it does not prove a copy is authentic. Identities are still string claims — v0.3 adds opt-in Ed25519-signed attestations and keyed audit verification (see Status), but key-to-identity binding remains out of band. Full semantics: [SPEC.md](./SPEC.md), "Agreement (v0.2)".

And the foreign-ledger audit is available on its own:

```python
v = stitch.audit_ledger("someone-elses.jsonl")
v.chain_ok, v.chain_broken_at, v.law_ok, v.law_excess, v.head_hash, v.entries
```

---

## Wire format

Every stitch appends exactly one line to the ledger JSONL file. Lines are `json.dumps(entry, separators=(",", ":"), sort_keys=True)` followed by `\n`.

```json
{"g":0.0,"h":"cbf29ce4842223a8","ns":"alice","op":"BIND","cell":"a3f2c901e8d7b4a1","kind":"task","payload":{"goal":"summarize"},"parent":null,"t":1698240000000000000}
```

| Field | Meaning |
|---|---|
| `t` | nanosecond unix time (UTC) |
| `ns` | tenant identity |
| `op` | one of BIND/LINK/EFFECT/VIEW/TICK/ATTEST/DELEGATE/CONTEST/MERGER/REVOKE/WITHDRAW |
| `g` | tenant's cumulative γ after this operation |
| `h` | FNV-1a 64-bit hash of this entry's content, seeded with the previous entry's hash — 16 hex chars |
| (others) | per-stitch fields — see SPEC.md |

The hash chain makes the ledger **tamper-evident**: changing any entry invalidates all subsequent hashes. The FNV-1a 64-bit canary hash is byte-exact across Python, Rust, TypeScript, and C99 implementations — any conformant implementation producing the same JSON line produces the same hash.

---

## Installation

```bash
# Option 1: copy-paste (it's pure Python, zero deps)
cp -r stitch-python/stitch /your/project/

# Option 2: pip install (when published)
pip install stitch-substrate
```

---

## Quick start

```python
import stitch

# Set up a tenant with the default log₂3 budget
alice = stitch.Tenant(identity="alice")

# Open an append-only ledger
ledger = stitch.Ledger(path="./state.jsonl")

# Use a surprise oracle (KL divergence between predicted and observed state)
surprise = stitch.KLSurprise()

# Bind a cell
task = stitch.bind(alice, ledger, kind="task", payload={"goal": "summarize PDF"})

# Predict what the task's status will be, then observe
predicted = {"status": "done"}
observed = {"status": "error"}
ok = stitch.effect(alice, ledger, surprise, task, "status", predicted, observed)
if not ok:
    print("Conservation law violated — write rejected")
    # Alice must tick (reset γ) or accept she's out of budget this tick

# View what got written
print(stitch.view(task))  # {"goal": "summarize PDF", "status": "error"}

# Advance time
stitch.tick(alice, ledger)  # γ resets to 0

# Verify the ledger's hash chain is intact
assert ledger.verify()
```

---

## What `stitch` IS

- A primitive library for accountable agent state
- Cross-language byte-exact (Python reference, Rust skeleton, TS/C99 planned)
- ~500 lines of code per language implementation
- Zero dependencies (Python stdlib only)
- A specification (`SPEC.md`) other languages can implement
- The smallest binding operation in a substrate

## What `stitch` IS NOT

- Not an LLM
- Not an agent runtime
- Not an orchestrator (no Border Collie, no router)
- Not a framework (no opinions about how you structure your agent)
- Not Thor-specific (works on any hardware)
- Not a database (the ledger is a file, not a service)
- Not the whole substrate (no quilt, no weaver, no JEV, no Border Collie)
- Not a replacement for vLLM / SGLang / LangChain / etc.

`stitch` is the *primitive* those systems could be built on. If you want the whole substrate (cells + weaver + JEV non-collapsing fields + Border Collie routing + idle-compile loop), this is one piece of it — the foundational piece, but only one piece.

---

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for where `stitch` sits in the larger Thor synthesis (substrate-foundation + quilt-chrono + quilt-dba + pasture-ai Border Collie + CudaClaw data plane + the 5-tier serving ladder).

## Specification

See [SPEC.md](./SPEC.md) for the canonical wire format, opcode definitions, hash computation, and byte-exactness requirements.

## Demo

```bash
python stitch-python/examples/minimal_agent.py
```

Runs a minimal "agent" (no LLM required — uses a fake deterministic responder) that binds a cell, predicts an outcome, observes it, records the surprise, demonstrates the conservation law rejecting a write, ticks to reset, and attests to the result.

## Tests

```bash
cd stitch-python && python -m pytest tests/ -v
```

Verifies the conservation law, the hash chain, byte-exactness, all 11 stitches, the v0.2 agreement layer (audit, minting, refusals, the head-hash anchor), and the v0.3 additions (non-ASCII pin, position-aware budget declarations, N-party agreement, Ed25519 + RFC 8032 vectors, opt-in signed attestations).

## License

MIT — see [LICENSE](./LICENSE).

## Status

v0.3.0 — adopted from the Z.ai (GLM) v0.3 draft after an independent assessment, with fixes (see [Provenance](#provenance)). The Python reference implementation passes **150 tests** (up from 54 in v0.2). The four rounds:

- **R1 — Non-ASCII encoding pin.** All `json.dumps` calls now use `ensure_ascii=False`, matching Rust's `serde_json` raw UTF-8 output. Closes the v0.2 known limitation where non-ASCII content hashed differently across implementations. 41 new byte-exactness tests with Latin-1, Chinese, Japanese, Korean, Arabic, Cyrillic, emoji, and math symbols. One hard break, honestly recorded: ledgers and payload hashes created under v0.2 with non-ASCII content do not verify under the pin (they never matched the Rust side either); ASCII is fully bidirectional.
- **R2 — Budget declarations.** New `declare_budget()` helper writes a `BIND` with `kind="tenant"` recording the per-tenant budget in the ledger. The audit reads declared budgets per-tenant instead of assuming one global budget. Backward-compatible: v0.2 ledgers without declarations still audit clean. **Adoption patch:** declarations are position-aware — a declaration covers entries from its own position onward, never earlier ones. The draft's audit extracted budgets in a pre-pass, so a declaration appended after an overspend retroactively excused it; a regression test now pins the refusal. 13 tests.
- **R3 — N-party agreement.** New `agree_n()` and `agreed_state_n()` functions returning `AgreementN`. The bilateral mirror rule generalises cleanly: every SEND booked in any ledger must have a mirror RECEIVE in some OTHER ledger, all bookings agree on the payload hash, and every ledger has at least one booking. A "party" is any ledger carrying at least one booking for the exchange; a receive-only ledger counts as a party, and minting does not require each party to send. For N=2 it reduces exactly to `agree`. 9 new tests including a 4-party ring.
- **R4 — Pure-Python Ed25519, wired in opt-in.** New `stitch.crypto` subpackage implementing RFC 8032 Ed25519 in pure Python (stdlib-only via `hashlib.sha512`). On adoption it was also wired in — narrowly, and off by default: `attest` writes a real Ed25519 signature when passed a secret key, and `audit_ledger` verifies such signatures when passed the tenants' public keys (`attest_keys`). Without keys on either side, entries and verdicts are byte-identical to v0.2. What this does **not** close: key-to-identity binding is still out of band, and only ATTEST entries can be signed — so copy authenticity is enforced exactly where a writer opted in and an auditor supplied keys, nowhere else. 22 module tests + 6 RFC-vector tests + 5 wiring tests.

The Rust crate implements the ledger, the hash chain, `Tenant`, the `bind` stitch, and the v0.2 audit; agreement/minting and the v0.3 features are Python-first. TypeScript and C99 are on the roadmap (v0.4).

**Ed25519 verification, settled on adoption.** The draft shipped the module with round-trip tests only and honestly said cross-library interop was unproven. During adoption the RFC 8032 §7.1 vectors (TEST 1, TEST 2) were pinned as tests — derived keys and signatures match the RFC byte-for-byte — and the implementation was cross-checked against OpenSSL in both directions. The module is conformant for the cases the vectors cover; its verify does not screen for small-order points (recorded in VERIFICATION.md).

See [VERIFICATION.md](./VERIFICATION.md) for the full test matrix and exactly what has and has not been verified.

The design is settled. The implementation will mature.

## Provenance

The v0.1 draft was written by Casey's Z.ai assistant (GLM) and sent to Muse by email on 2026-10-26, with the instruction to try to make it into something legitimate. Muse verified the claims, fixed what was broken, ran the cross-machine test matrix, and prepared this repository for publication. Verification results — including the failures found on the way — are recorded in [VERIFICATION.md](./VERIFICATION.md), not smoothed over.

v0.2 (the agreement layer) was designed and built by Muse on the v0.1 base, and verified on five machines the same day. The v0.3 draft was written by Casey's Z.ai assistant (GLM), building on the published v0.2 and aiming at its four named gaps; Muse assessed it independently (the assessment is summarised in VERIFICATION.md) and adopted it with fixes: the position-aware budget patch, the opt-in attest/audit signature wiring, the RFC 8032 vectors pinned as tests, corrected doc claims, and the version bump completed. The Rust crate remains as of v0.2; the v0.3 features are Python-first.
