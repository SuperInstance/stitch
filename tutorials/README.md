# Tutorials & templates

These are not toy examples. Each folder is a **working template for a
real job** people use stitch for, structured so you can run it as-is,
watch it do the thing, then adapt the marked parts to your own work.
Every script is stdlib-only, runs offline, and was executed end-to-end
to produce the outputs quoted in its README.

| Tutorial | The real job | What it teaches |
|---|---|---|
| [01 — The accountable journal](01-accountable-journal/) | One agent does work; a supervisor must be able to audit it later without trusting the agent or its machine | ledgers, the conservation law at write time, both budget knobs, rejection-as-normal-flow, `audit_ledger` |
| [02 — The agent-to-agent handoff](02-agent-handoff/) | Two agents on different machines exchange a work product; both need a receipt neither can fake alone | bookings, payload-as-contract, `agree`, refusal reasons, the head-hash anchor |
| [03 — Corroborated observers](03-corroborated-readings/) | Independent observers/devices record the same event offline; later, only the corroborated version should count | sovereign ledgers, file-copy sync, per-observer minting, quorum as an application-level fold, tamper exclusion |

New here? Do them in order — each builds on the previous one's ideas.
Adaptation points inside the tutorial code are marked `# ADAPT:` —
grep for it.

## Before any tutorial: five things that will save you an hour

1. **Getting `import stitch` to work.** Inside this repo, every
   tutorial script inserts `stitch-python/` into `sys.path` itself, so
   they just run. In your own project, pick one route and make it true
   *before* the first code block you copy: `pip install ./stitch-python`,
   copy the `stitch/` package into your project, or set
   `PYTHONPATH=…/stitch-python`. Beware the trap: if your working
   directory contains a folder literally named `stitch`, a bare
   `import stitch` can silently bind to *that folder* instead of the
   library and fail later with `AttributeError: … no attribute 'Tenant'`.
2. **There are two budget knobs; set both.** `Tenant(identity=…, budget=C)`
   is what the conservation law **enforces** when you write.
   `declare_budget(tenant, ledger)` **records** that choice in the
   ledger so an auditor sees it. Declaring alone does not raise the
   budget your writes are held to — this is the single most common
   wrong mental model new users form.
3. **Ledger lifecycle.** Every append is flushed to disk as it happens,
   so another process can audit a ledger while its writer is alive —
   but call `ledger.close()` (or use `with`) when you're done, and
   keep **one writer per ledger file**: two processes appending to one
   file can interleave and corrupt the chain.
4. **A refusal is a value, not an exception.** `agree()` and friends
   return verdict objects. The fields you will actually use:
   `.agreed` (bool), `.reason` (one of `"agreed"`, `"no_send"`,
   `"missing_mirror"`, `"peer_mismatch"`, `"payload_mismatch"`,
   `"chain_broken"`, `"law_violated"`), `.payload_hash` (set when
   minted), `.detail` (human-readable specifics), and the head pins
   `.head_a` / `.entries_a` / `.head_b` / `.entries_b` for
   `verify_anchor`.
5. **One `effect` writes one key.** The entry records
   `v = observed[key]`, and the cell's `payload[key]` becomes that
   value — while surprise is charged over the *whole* predicted and
   observed dicts. Every example pairs the key with single-key dicts,
   which hides the rule until you journal two facts in one call with a
   key that names neither: that commits, charges γ, passes audit, and
   silently records `v: null`. To record two facts, make two `effect`
   calls (and budget for both). (Found by cold test #2, 2026-10-27.)
6. **What agreement does not prove.** Agreement proves two *records*
   booked the same thing in ledgers that pass audit. It does not prove
   a ledger copy is authentic, that a payload ever moved, or that a
   reading is true. Identity is a string claim; signed attestations
   (SPEC, "Ed25519 signatures") narrow one part of that gap when a
   writer signs *and* an auditor supplies the public key. Every
   tutorial's README repeats the part of this that applies to it.
