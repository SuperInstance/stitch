# Research Synthesis — Round 3

This document is the synthesis of a 12-agent scouting mission (6 lanes × 2 rounds) conducted to inform the design of `stitch`. The full worklog (1,030 lines, all 12 agent entries) is in `worklog.md`. This is the digest.

---

## Headline finding

The user's portfolio is **vastly larger than the 15 known repos suggest**: **4,994 public repos** across 177 paginated pages, with **1,176 repos pushed in the last 3 months** (hyperactive single-maintainer account). The portfolio is organized as a cellular substrate — most repos are tiny byte-exact cross-language ports of a shared primitive set, with a few large orchestration repos on top.

The most important finding: **this is a composition project, not an invention project.** R2-C estimates ~70% of the non-collapsing architecture already exists in-portfolio. R2-F says "the user's own portfolio contains every primitive the weaver needs." The synthesis job is to identify the canonical 30-40 foundational repos and wire them together with Thor's hardware features.

---

## The 6 lanes scouted

1. **R1-A / R2-A**: Full SuperInstance GitHub account reconnaissance (4,994 repos)
2. **R1-B / R2-B**: Multi-agent collaboration SOTA (Microsoft Agent Framework, OpenAI Agents SDK, LangChain Deep Agents, MCP, A2A)
3. **R1-C / R2-C**: JEPA, world models, non-collapsing inference theory (V-JEPA 2, Dreamer RSSM, SVGD)
4. **R1-D / R2-D**: Jetson Thor hardware + edge LLM serving tooling (TensorRT-LLM, vLLM, SGLang, MIG, NVFP4)
5. **R1-E / R2-E**: Conservation laws, thermodynamics, market allocation (VCG, DRF, surprise-bits currency)
6. **R1-F / R2-F**: Idle-compute, speculative compilation, agent self-evolution (Voyager, STaR, agent-riff-v3)

---

## The synthesized 6-layer architecture

```
L6  User-facing surface (TUI / Web / API — pasture-ai modes)
L5  Border Collie orchestrator (pasture-ai, Rust+TRT-LLM)
L4  5-tier serving ladder:
      T0 CudaClaw       sub-µs reflex, zero-LLM, owns own TPC
      T1 TensorRT-LLM   latency, NVFP4
      T2 vLLM           throughput, multi-LoRA hot-swap
      T3 SGLang         structured generation
      T4 OpenShell      untrusted student code (skip NemoClaw)
      T5 lane-sheets    structural, no LLM
L3  Weaver (jev-garden + luciddreamer-py) on dedicated MIG TPC
      Verifier = jev-lattice frozen Rust + agent-riff-v3 BootstrapVerifier
L2  Quilt substrate (substrate-foundation + quilt-chrono + quilt-dba + quilt-jetson)
      11 opcodes, append-only time ledger, γ+η≤log₂3 per-tenant
      Multi-tenant via Linux namespaces + cell-ns prefixes (NOT just MIG)
L1  ★ stitch ★ (the primitive — what we extracted as the library)
      11 stitches, conservation law, hash-chained ledger, surprise oracle
L0  Jetson Thor T5000: Blackwell + 128GB unified @273GB/s + MIG across 10 TPCs
```

---

## Ten cross-cutting decisions resolved by Round 2

1. **Layering is strict** — substrate-foundation → continuum-runtime (missing middle) → Border Collie → serving → CudaClaw
2. **CudaClaw is the data plane, not an LLM engine** — must own its own TPC (persistent kernel)
3. **MIG ≠ multi-tenant isolation** — all slices share 128GB unified memory; need Linux namespaces + per-tenant Quilt cell-ns prefixes on top
4. **MIG partition is 1+5+2+2** — γ+OS / serving / compile / escalation
5. **Non-collapsing state is the user's original synthesis** — no academic term for it, but ~70% in-portfolio; action is a posterior SAMPLE not argmax/MAP
6. **Collapse gate is KL-thresholded** — surprise-bits (KL divergence predicted↔observed) IS the formal collapse signal
7. **γ+η≤log₂3 is per-tenant, design axiom** — Shannon info of one trit, not Landauer-derived
8. **Currency = surprise-bits, non-fungible** — sponsor-able downward only; DRF + lottery cold-start + Vickrey surplus auction when ≥4 tenants bid
9. **Verification is a 3-layer onion** — surprise-bits (cost) + 3-gate cascade (gate structure) + Deep Agents rubrics (assessment language); verifier substrate-external
10. **Weaver gets dedicated MIG TPC** — luciddreamer-py IS the distillation arm; multi-Thor weaver = local + fleet murmur merge bounded by d+1 tissue cap

---

## Why we extracted `stitch` as the killer primitive

The user asked: "strip this down to what is killer-something potential. we don't need to be the application we are building. we need to be the tool that could go invisible like infrastructure could."

The substrate primitive is the only piece that, if it disappeared, every layer above would notice. The Border Collie is an orchestrator (application). The weaver is a scheduler (application). The JEV fields are an epistemology (application). The serving ladder is a runtime (application). None of those goes invisible — they're all applications.

The substrate goes invisible because:
- It's the load-bearing layer (every other component reads or writes to it)
- It's language-portable (TS/Rust/Python/C99 already exist in the portfolio)
- It encodes a novel design philosophy (conservation law + surprise budget — not in any other agent-state library)
- It's small enough to be invisible (~500 lines per language, zero deps)
- It composes with everything (LangChain, AutoGen, custom Python, Rust services, browser apps)

`stitch` is the canonical, language-portable distillation of:
- substrate-foundation's 11 opcodes → stitch's 11 stitches
- quilt-chrono's time ledger → stitch's `Ledger` with `TICK`
- quilt-dba's γ+η≤log₂3 conservation law → stitch's `Tenant` with per-tick budget
- quilt-dba's double-entry ledger → stitch's hash-chained JSONL
- quilt-dba's sticky scars → stitch's `EFFECT` entries (append-only)
- substrate-foundation's FNV-1a 64-bit canary hash → stitch's hash chain seed

What `stitch` deliberately *excludes* (because those are higher-layer concerns):
- The hex lattice (ExoJ) — that's L3 (the weaver / non-collapsing field)
- The Border Collie (pasture-ai) — that's L5 (orchestrator)
- The serving ladder — that's L4
- The idle-compile loop (jev-garden) — that's L3
- The verifier cascade — that's L3
- Multi-tenant isolation — that's an OS concern
- The JEV/JEPA epistemology — that's L3

The library is the load-bearing wall. Build the rest on top.

---

## Remaining open questions (couldn't be resolved from literature/portfolio)

1. **Hardware verification.** User's actual JetPack version + QSFP28 silicon (ConnectX-7 / BlueField-3 / custom HSB?). Affects every engine version lock. Run: `nvcc --version`, `lspci -v | grep -i mellanox`, `nvidia-smi -q`, `cat /etc/nv_tegra_release`.
2. **Does SVGD-on-hex compose cleanly with cudaclaw's warp-dispatch?** R2-C's biggest open risk. Both want to manage particle sets over the lattice.
3. **Continuum-runtime** — does it already exist in the portfolio under another name? R2-A's hypothesis: the missing middle layer between substrate-foundation and pasture-ai. Could be one of the ~4,950 unseen repos.
4. **Cold-start policy** — on day one, no credits exist, no breeds exist, no harnesses exist. R2-E addresses this with γ_pending but the actual genesis allocation is undefined.
5. **Multi-Thor fleet topology** — single Thor is one box. When does the user add Thor #2? Local weavers + fleet murmur merge per R2-F, but the actual mesh topology (star? full-mesh? witness-anchor-mediated?) is undefined.

---

## Week One path (post-`stitch`)

Once `stitch` is shipped as the foundation:

1. **Day 1**: hardware reality check (`nvcc --version`, `lspci -v`, `nvidia-smi -q`)
2. **Day 2**: clone the foundational repos (substrate-foundation, quilt-jetson, cudaclaw, pasture-ai, exoj, jev-garden, agent-riff-v3, luciddreamer-py). Build each on Thor.
3. **Day 3**: stand up the layering on top of `stitch` (continuum-runtime stub + pasture-ai Border Collie + vLLM + CudaClaw). Two breeds (7B + 30B) in unified memory.
4. **Day 4**: one task — "summarize a PDF." Single tenant. Use `stitch` for accountable state. JSONL ledger.
5. **Day 5**: run the weaver once at night. **Inspect the weave artifact. If it's garbage, the scaling story is in trouble — and you want to know in week one, not month three.**
6. **Day 6**: add tenant #2. Watch the shared-memory isolation break.
7. **Day 7**: introduce Vickrey auction for surplus GPU time-slices.

If days 1-5 work, the rest of the architecture (Border Collie forest routing, agent-riff-v3 spec generation, multi-Thor mesh, NemoClaw-style sandboxing) is features you earn the right to build after the boring version works.

---

## The single question back to the user

The architecture has a clean shape and `stitch` is the foundation. The thing I genuinely can't answer without you: **does the actual Thor hardware you have match what R1-D documented?** Specifically — is it a T5000 SoM (production Thor silicon) or are you on an AGX Orin devkit carrier running a JetPack-for-Thor containerized preview? If it's the latter, the MIG 10-TPC partition policy and the NVFP4 70B-fit assumptions all need to be re-checked.

Run `nvidia-smi -q | head -40` and `lspci -v | grep -i mellanox` and tell me what you see. That pins everything downstream.

The full 1,030-line worklog with all 12 agent entries is at `worklog.md`.
