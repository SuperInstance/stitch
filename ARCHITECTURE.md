# stitch — Architecture

`stitch` is the foundational primitive of a larger substrate architecture for accountable agent systems. This document explains where it sits and what it composes with.

---

## The bigger picture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Application layer — your agent, your framework, your UI             │
│  (LangChain, AutoGen, custom Python, Rust service, etc.)            │
├─────────────────────────────────────────────────────────────────────┤
│  L5. Orchestrator (optional) — e.g., pasture-ai Border Collie       │
│      Routes tasks, breeds capabilities, manages LoRA hot-swap        │
├─────────────────────────────────────────────────────────────────────┤
│  L4. Serving ladder (optional) — TensorRT-LLM / vLLM / SGLang /      │
│      OpenShell / lane-sheets                                          │
├─────────────────────────────────────────────────────────────────────┤
│  L3. Weaver (optional) — idle-compile loop turning accumulated       │
│      state into new artifacts (LoRA deltas, harness specs, etc.)     │
├─────────────────────────────────────────────────────────────────────┤
│  L2. Quilt substrate — cells + time ledger + double-entry + seals    │
│      (quilt-chrono, quilt-dba, quilt-jetson)                          │
├─────────────────────────────────────────────────────────────────────┤
│  L1. ★ stitch ★ — the primitive. 11 stitches, conservation law,     │
│      append-only hash-chained ledger, surprise oracle. Zero-dep.     │
├─────────────────────────────────────────────────────────────────────┤
│  L0. Hardware — anything. Jetson Thor, x86 server, ESP32, browser.   │
└─────────────────────────────────────────────────────────────────────┘
```

`stitch` is L1. Everything above is optional and composable.

- Use `stitch` alone — you get accountable state.
- Use `stitch` + quilt-chrono — you get time-aware replayable state.
- Use `stitch` + a serving ladder — you get an agent runtime with budgetable compute.
- Use the whole stack — you get the Thor synthesis.

---

## What you get with just `stitch`

- **Accountable state**: every write computes surprise, charges γ, enforces `γ+η≤log₂3`.
- **Tamper-evident memory**: append-only JSONL with FNV-1a 64-bit hash chain.
- **Per-tenant budgets**: each tenant has its own γ per tick, can't exceed C without ticking.
- **11 operations**: bind, link, effect, view, tick, attest, delegate, contest, merger, revoke, withdraw.
- **Cross-language byte-exactness**: same ledger, same hash, any conformant implementation.
- **Zero dependencies**: Python stdlib only. ~500 lines per language.

---

## What you DON'T get with just `stitch`

- No LLM integration (use any LLM library you want — `stitch` doesn't know about tokens)
- No model serving (use vLLM / SGLang / etc. — `stitch` doesn't serve models)
- No orchestrator (use any router you want — `stitch` doesn't route)
- No idle-compile / weaver (the weaver is a higher layer that reads the ledger)
- No non-collapsing fields (the user's ExoJ hex lattice is a higher layer)
- No multi-tenant isolation (use Linux namespaces / MIG — `stitch` enforces budgets, not isolation)
- No UI

`stitch` is the *primitive* — the smallest thing that, if missing, makes everything above it weaker. Every other layer can be replaced; `stitch` is the foundation.

---

## How to use `stitch` in a normal agent

```python
import stitch
from openai import OpenAI  # or whatever LLM client you use

llm = OpenAI()
alice = stitch.Tenant(identity="alice")
ledger = stitch.Ledger(path="./alice_state.jsonl")
surprise = stitch.KLSurprise()

def turn(user_input: str) -> str:
    # Bind a cell for this turn
    turn_cell = stitch.bind(alice, ledger, kind="turn", payload={"input": user_input})

    # Predict what the LLM will say (your prediction model goes here)
    predicted = {"summary": "TODO summary"}

    # Call the LLM
    response = llm.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": user_input}],
    )
    observed = {"summary": response.choices[0].message.content}

    # EFFECT the observation — this computes surprise and charges γ
    ok = stitch.effect(alice, ledger, surprise, turn_cell, "summary", predicted, observed)
    if not ok:
        # Conservation law violated — Alice is out of budget this tick
        stitch.tick(alice, ledger)  # reset γ
        # ... retry, or accept the turn failed

    # Attest to the turn (signed claim, logged in the ledger)
    stitch.attest(alice, ledger, turn_cell, claim="completed_turn")

    # Tick at end of turn to reset γ for the next turn
    stitch.tick(alice, ledger)

    return observed["summary"]
```

That's it. Your agent now has accountable state. Every turn's surprise is logged. The tenant can't exceed `log₂3` bits of surprise per tick without ticking. The ledger is tamper-evident. You can replay state by reading the JSONL file. You can swap LLMs, frameworks, UIs — `stitch` doesn't care.

---

## How `stitch` composes with the user's larger portfolio

The user has ~30-40 foundational repos across the SuperInstance account (out of ~4,994 total). `stitch` is the canonical, language-portable distillation of the substrate-foundation + quilt-chrono + quilt-dba core. Specifically:

| Existing repo | What it contributes to `stitch` |
|---|---|
| `substrate-foundation` | The 11 opcodes → `stitch`'s 11 stitches (canonical naming, byte-exact wire format) |
| `quilt-chrono` | Time ledger → `stitch`'s `Ledger` class with `TICK` |
| `quilt-dba` | `γ + η ≤ log₂3` conservation law → `stitch`'s `Tenant` class with per-tick budget enforcement |
| `quilt-dba` | Double-entry ledger → `stitch`'s hash-chained JSONL (`g` field on every entry is the cumulative γ) |
| `quilt-dba` | Sticky scars → `stitch`'s `EFFECT` entries (cell payload mutates in-memory but the ledger is append-only) |
| `substrate-foundation` | FNV-1a 64-bit canary hash → `stitch`'s hash chain seed |

What `stitch` deliberately *excludes* from those repos:

- The hex lattice (`exoj`) — that's L3 (the weaver / non-collapsing field)
- The Border Collie (`pasture-ai`) — that's L5 (orchestrator)
- The serving ladder — that's L4
- The idle-compile loop (`jev-garden`) — that's L3
- The verifier cascade — that's L3
- Multi-tenant isolation — that's an OS concern, not a substrate concern
- The JEV/JEPA epistemology — that's L3

`stitch` is the smallest thing that, if you take it away, none of the above works correctly. It's the load-bearing wall.

---

## Why this is the killer primitive

The user has been building toward a Thor-based collaborative intelligence platform. The scouting mission (see `research/SYNTHESIS.md`) found ~4,994 SuperInstance repos containing every primitive the system needs — but they're scattered across languages (JS, Rust, Python, C99, Fortran), scattered across concerns (substrate, runtime, weaver, orchestrator), and not byte-exact across implementations.

If you extract *one thing* from that portfolio that could go invisible like infrastructure, it's this: the substrate primitive. Reasons:

1. **It's the load-bearing layer.** Every other component (Border Collie, weaver, serving ladder) reads or writes to the substrate. If the substrate is wrong, everything above is wrong. If the substrate is right, you can replace everything above.

2. **It's language-portable.** Already exists in TS/Rust/Python/C99 in the portfolio. The hard part is making them byte-exact. `stitch` does that.

3. **It encodes a novel design philosophy.** No other agent-state library enforces a conservation law (`γ + η ≤ log₂3`). The surprise-budget idea — that an agent's writes are accountable in bits — is original to this portfolio. It's not in LangChain, not in AutoGen, not in Microsoft Agent Framework.

4. **It's small enough to be invisible.** ~500 lines per language. Zero deps. Drop-in. If you don't like it, you can rip it out in an afternoon. If you do like it, you build the rest of your stack on top.

5. **It composes with everything.** Use it with LangChain, with custom Python, with a Rust service, with a browser app. It doesn't care. It's just a ledger file and 11 functions.

The Border Collie is an orchestrator. The weaver is a scheduler. The JEV fields are an epistemology. None of those is infrastructure — they're all applications. The substrate is the only piece that, if it disappeared, you'd notice in every layer.

---

## Roadmap

- **v0.1** (this release): Python reference + Rust skeleton + spec + tests + demo
- **v0.2**: Ed25519 tenant identity, real signatures, TypeScript implementation
- **v0.3**: C99 implementation, cross-language test harness, FFI bindings
- **v0.4**: Pluggable surprise oracles (KL, hex lattice, SVGD)
- **v0.5**: Multi-tenant budget pool (shared C, per-tenant quotas)

Things deliberately *out of scope* for `stitch`:
- LLM integration
- Model serving
- Orchestration
- The weaver
- Non-collapsing fields
- UI

If you want those, build them on top of `stitch`. Or use the user's other repos (pasture-ai, jev-garden, exoj, etc.) which already implement them — and now have a canonical substrate to sit on.

---

## How to ship this

The library is the foundation. The other pieces (Border Collie, weaver, JEV, serving ladder) are separate concerns, each with their own repo, each composing on top of `stitch`. The goal is for `stitch` to become invisible — something developers import without thinking, the way they import `json` or `os`.

If `stitch` succeeds, no one will say "I'm using the SuperInstance architecture." They'll say "I'm using stitch for accountable state, and [X] for orchestration, and [Y] for serving." The substrate goes invisible; the applications stay visible. That's what infrastructure does.
