# stitch

A tiny, language-portable substrate primitive for accountable agent state.

`stitch` is the smallest binding operation in a quilt. This library provides stitches: 11 operations on cells of agent state, each one append-only, conservation-law-enforced, and byte-exact across languages.

> The smallest thing that, if missing, makes every layer above it weaker. Not an LLM, not a runtime, not a framework. A primitive.

---

## The design principle: simplicity is what makes it scale

`stitch` is deliberately small, and the smallness is not an aesthetic choice — it is the scaling strategy. Every operation is a **local, append-only write to your own ledger**. Nothing in the write path waits on anyone else:

- **No central sequencer or coordinator.** A tenant writes to its own ledger file. There is no server to ask, no leader to elect, no lock to take.
- **No global clock.** The `t` field is the writer's local observation of time. The only ordering that matters is the order of entries inside one ledger, and that is carried by the hash chain itself — not by comparing timestamps across machines.
- **No synchronous agreement.** Each side keeps its own sovereign record. Verification (replaying a hash chain) and agreement (two sides comparing what they each recorded) can happen later, whenever the records meet — minutes or months after the writes. Shared state is what the records agree on; it is minted by that agreement, not by a store both sides had to touch at write time.
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

Verifies the conservation law, the hash chain, byte-exactness, and all 11 stitches.

## License

MIT — see [LICENSE](./LICENSE).

## Status

v0.1.0 — initial public release. The Python reference implementation is complete (39 tests). The Rust crate implements the ledger, the hash chain, `Tenant`, and the `bind` stitch; the remaining stitches are not yet implemented there. TypeScript and C99 are on the roadmap (v0.2, v0.3).

Known v0.1 limitation: byte-exactness between the Python and Rust ledgers is verified for ASCII content. Python's `json.dumps` escapes non-ASCII characters by default while Rust's `serde_json` emits raw UTF-8, so entries containing non-ASCII text hash differently across the two implementations until the encoding is pinned (planned for v0.2). See [VERIFICATION.md](./VERIFICATION.md) for the full test matrix and exactly what has and has not been verified.

The design is settled. The implementation will mature.

## Provenance

The v0.1 draft was written by Casey's Z.ai assistant (GLM) and sent to Muse by email on 2026-10-26, with the instruction to try to make it into something legitimate. Muse verified the claims, fixed what was broken, ran the cross-machine test matrix, and prepared this repository for publication. Verification results — including the failures found on the way — are recorded in [VERIFICATION.md](./VERIFICATION.md), not smoothed over.
