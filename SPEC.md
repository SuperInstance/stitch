# stitch — Canonical Specification v0.1

This document is the canonical specification for the `stitch` substrate primitive. Any implementation in any language must produce byte-exact ledger output matching this spec to be conformant.

---

## Constants

```python
LOG2_3   = 1.584962500721156    # The per-tick per-tenant conservation budget
FNV_OFFSET = 0xcbf29ce484222325 # FNV-1a 64-bit offset basis
FNV_PRIME  = 0x100000001b3      # FNV-1a 64-bit prime
MASK_64    = 0xFFFFFFFFFFFFFFFF
LEDGER_VERSION = 1
```

---

## The conservation law

For each tenant, each tick:

```
γ + η ≤ C
```

where:
- `γ` = cumulative surprise dissipated this tick (a non-negative float, in bits)
- `η` = headroom remaining = C − γ (a non-negative float, in bits)
- `C` = per-tenant budget (default: `log₂3 ≈ 1.585`)

### Enforcement

The `EFFECT` stitch computes `s = surprise(predicted, observed)`. If `γ + s > C`, the write is **rejected** — no ledger entry is appended, `γ` is unchanged, and the call returns `False`. Otherwise `γ := γ + s`, the cell's payload is mutated in-memory, and a ledger entry is appended with `s` recorded.

The `TICK` stitch resets `γ := 0` for the tenant. (Note: the ledger is append-only; TICK does not delete prior γ records, it just resets the tenant's in-memory counter for the next tick.)

The `WITHDRAW` stitch does not change `γ` — it releases headroom `η` back to the pool (a no-op for single-tenant deployments, but in multi-tenant deployments with a shared budget, it returns η for other tenants to bid on).

---

## Wire format

Each stitch (except `VIEW`, which is free and not logged) appends exactly one line to the ledger JSONL file. The line is:

```
<json_entry> + "\n"
```

where `<json_entry>` is `json.dumps(entry, separators=(",", ":"), sort_keys=True)`.

### Common fields

Every entry has:

| Field | Type | Meaning |
|---|---|---|
| `t` | int | nanosecond unix time (UTC) |
| `ns` | str | tenant identity |
| `op` | str | one of the 11 stitch names (uppercase) |
| `g` | float | tenant's γ after this operation (cumulative for the current tick) |
| `h` | str (16 hex chars) | FNV-1a 64-bit hash of this entry's content (excluding `h` itself), seeded with the previous entry's hash (or `FNV_OFFSET` for the first entry) |

### Per-stitch fields

| Stitch | Extra fields |
|---|---|
| `BIND` | `cell` (str), `kind` (str), `payload` (any JSON), `parent` (str or null) |
| `LINK` | `a` (str), `b` (str), `rel` (str) |
| `EFFECT` | `cell` (str), `k` (str), `v` (any JSON), `s` (float) |
| `VIEW` | (no ledger entry — reads are free) |
| `TICK` | (only common fields) |
| `ATTEST` | `cell` (str), `claim` (str), `sig` (str) |
| `DELEGATE` | `cell` (str), `to` (str), `scope` (str) |
| `CONTEST` | `cell` (str), `reason` (str) |
| `MERGER` | `cell` (str), `a` (str), `b` (str), `kind` (str) |
| `REVOKE` | `cell` (str), `reason` (str) |
| `WITHDRAW` | `amount` (float) |

---

## Hash computation

For an entry `E`, the hash field `h` is computed as:

```python
import json

def compute_hash(entry: dict, previous_hash: int | None) -> str:
    # Remove the h field if present (it's not part of the hashed content)
    content = {k: v for k, v in entry.items() if k != "h"}
    # Serialize: sorted keys, no whitespace, no extra spaces
    line = json.dumps(content, separators=(",", ":"), sort_keys=True)
    # Seed with previous hash (or FNV_OFFSET for the first entry)
    seed = previous_hash if previous_hash is not None else FNV_OFFSET
    h = fnv1a_64(line, seed)
    return f"{h:016x}"

def fnv1a_64(data: str, seed: int) -> int:
    h = seed & MASK_64
    for b in data.encode("utf-8"):
        h ^= b
        h = (h * FNV_PRIME) & MASK_64
    return h
```

The hash is stored as a 16-character lowercase hex string. The next entry's hash is seeded with this hash interpreted as a 64-bit unsigned int.

---

## Surprise oracle

The default surprise oracle is a discrete KL divergence over the key space of two dicts.

For `predicted: dict` and `observed: dict`:

```python
def surprise(predicted: dict, observed: dict) -> float:
    all_keys = set(predicted.keys()) | set(observed.keys())
    if not all_keys:
        return 0.0
    s = 0.0
    for k in all_keys:
        p_in = k in predicted
        o_in = k in observed
        if p_in and o_in:
            if predicted[k] != observed[k]:
                s += 1.0  # 1 bit of surprise per mismatched value
        elif o_in and not p_in:
            s += 1.0  # 1 bit of new information
        # predicted-only: no surprise (predicted was over-complete)
    return s
```

This is intentionally simple. Production deployments may swap in:
- real KL divergence over continuous distributions
- hex-lattice deformation (ExoJ pattern)
- SVGD particle set divergence
- learned surprise models

The library's `Surprise` class is the seam — implementations should be swappable without changing the rest of the API.

---

## Tenant identity

In v0.1, tenant identity is a string (any UTF-8). In v0.2, it will be an Ed25519 public key (32 bytes, hex-encoded). The `ATTEST` stitch's `sig` field will be a real Ed25519 signature in v0.2; in v0.1 it is a SHA-256 hash placeholder.

---

## Cell ID

A cell's ID is the first 16 hex characters of:

```
sha256(tenant_identity + ":" + kind + ":" + json.dumps(payload, sort_keys=True, separators=(",",":")) + ":" + (parent_hash or "") + ":" + str(timestamp_ns))
```

This is not cryptographically unique (16 hex = 64 bits), but it is collision-resistant in practice for a single ledger. A real deployment would use a longer hash or a UUID.

---

## Byte-exactness

All conformant implementations MUST produce identical `h` values for identical inputs. The test suite (`tests/cross_lang.py`) verifies this by:

1. Generating a fixed sequence of stitches with deterministic timestamps.
2. Running the sequence through each language implementation.
3. Comparing the resulting ledger files byte-for-byte.

Any implementation that produces a different hash for the same input is non-conformant.

The determinism rules:
- JSON output must use sorted keys, `(",", ":")` separators, no extra whitespace.
- All numbers must serialize the same way (Python's `json.dumps` is the reference; floats use `repr(float)`-style).
- UTF-8 encoding everywhere.
- FNV-1a 64-bit arithmetic must use 64-bit unsigned wraparound (mask with `0xFFFFFFFFFFFFFFFF`).

### No global clock, no coordinator

Conformance never depends on synchrony. The `t` field is the writer's local observation; verifiers MUST NOT compare `t` values across ledgers or require them to be monotonic across writers. The hash chain is the only ordering a conformant implementation needs, and it is internal to one ledger. A ledger can be verified at any time after it was written — verification and cross-ledger agreement are asynchronous operations that happen when records meet, not conditions on writing. Nothing in this spec requires a central sequencer, a coordinator, or a shared clock, and implementations MUST NOT introduce one into the write path.

### Known limitation (v0.1): non-ASCII content

The Python reference serializes with `json.dumps` defaults, which escape non-ASCII characters (`ensure_ascii=True`); the Rust implementation's `serde_json` emits raw UTF-8. Entries containing non-ASCII text therefore produce different bytes — and different hashes — across the two implementations. Byte-exactness in v0.1 is verified for ASCII content only. v0.2 must pin one encoding rule for all implementations.

---

## Implementation status

| Language | Status | Notes |
|---|---|---|
| Python | ✅ Reference | `stitch-python/` — full implementation, tests pass |
| Rust | 🚧 Skeleton | `stitch-rs/` — core types + 2 stitches, demonstrates cross-lang intent |
| TypeScript | 🚧 Planned | v0.2 |
| C99 | 🚧 Planned | v0.3 |

---

## Changelog

- **v0.1** (2026-10-26): Initial public spec. 11 stitches, FNV-1a 64-bit hash chain, discrete KL surprise oracle. Python reference implementation. Rust skeleton.
- **v0.2** (planned): Ed25519 tenant identity, real signatures, TypeScript implementation.
- **v0.3** (planned): C99 implementation, cross-language test harness, FFI bindings.
- **v0.4** (planned): Pluggable surprise oracles (KL, hex lattice, SVGD).
- **v0.5** (planned): Multi-tenant budget pool (shared C, per-tenant quotas).

---

## What is explicitly out of scope for `stitch`

- LLM integration (use any LLM library; `stitch` doesn't know about tokens)
- Model serving (use vLLM / SGLang / etc.)
- Orchestration / routing (use any router; `stitch` doesn't route)
- Idle-compile / weaver (a higher layer that reads the ledger)
- Non-collapsing fields (the ExoJ hex lattice is a higher layer)
- Multi-tenant isolation (use Linux namespaces / MIG; `stitch` enforces budgets, not isolation)
- UI
- Anything Thor-specific

If you want those, build them on top of `stitch`. Or use the user's other repos (pasture-ai, jev-garden, exoj, etc.) which already implement them.
