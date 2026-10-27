# stitch — Canonical Specification v0.3

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

Tenant identity is a string (any UTF-8). An earlier roadmap draft planned Ed25519 public-key identities and real `ATTEST` signatures for v0.2; that is deferred. Ed25519 is not in the Python standard library, and stitch's zero-dependency rule outranks it — a hand-rolled signature implementation would be worse than an honest deferral. Consequence, stated plainly: in v0.2 an identity is a *claim*, not a proof. The agreement rules in the next section prove that two records agree; they do not prove who wrote a record. The `ATTEST` stitch's `sig` field remains a SHA-256 hash placeholder.

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
- **Non-ASCII content is emitted as raw UTF-8 bytes, not `\uXXXX` escapes.** Python implementations MUST pass `ensure_ascii=False` to every `json.dumps` call that produces a hashed line or a payload commitment; Rust's `serde_json` already emits raw UTF-8 by default. This is the v0.3 encoding pin — it closes the v0.2 known limitation where entries containing non-ASCII text hashed differently across implementations.
- FNV-1a 64-bit arithmetic must use 64-bit unsigned wraparound (mask with `0xFFFFFFFFFFFFFFFF`).

The byte-exactness pin applies to:
- `Ledger.append` (the line written to the file, with and without `h`)
- `Ledger.verify` and `Ledger._compute_last_hash` (re-serialization for hash recomputation)
- `payload_hash` (the canonical form hashed by SHA-256 for exchange commitments)
- `_audit` in `agreement.py` (re-serialization for hash chain verification of foreign ledgers)
- `_cell_id` (the canonical form of the payload that goes into the cell ID material)

### No global clock, no coordinator

Conformance never depends on synchrony. The `t` field is the writer's local observation; verifiers MUST NOT compare `t` values across ledgers or require them to be monotonic across writers. The hash chain is the only ordering a conformant implementation needs, and it is internal to one ledger. A ledger can be verified at any time after it was written — verification and cross-ledger agreement are asynchronous operations that happen when records meet, not conditions on writing. Nothing in this spec requires a central sequencer, a coordinator, or a shared clock, and implementations MUST NOT introduce one into the write path.

### Non-ASCII content (closed in v0.3)

**v0.2 limitation, now closed.** The Python reference previously used `json.dumps` defaults, which escape non-ASCII characters (`ensure_ascii=True`); the Rust implementation's `serde_json` emits raw UTF-8. Entries containing non-ASCII text therefore produced different bytes — and different hashes — across the two implementations.

**v0.3 pin.** All conformant implementations MUST emit non-ASCII string content as raw UTF-8 bytes, not as `\uXXXX` escapes. Concretely:

- Python: every `json.dumps` call that produces a hashed line, a payload commitment, or a cell ID material component MUST pass `ensure_ascii=False`.
- Rust: `serde_json::to_string` already emits raw UTF-8 by default — no change required.
- TypeScript (roadmap): `JSON.stringify` emits raw UTF-8; matches the pin.
- C99 (planned v0.4): a conformant serializer MUST NOT escape non-ASCII characters in string contents; only control characters (< 0x20), `\`, and `"` are escaped.

Control characters (`\b`, `\f`, `\n`, `\r`, `\t`, and other bytes < 0x20) MUST still be escaped as `\b`, `\f`, `\n`, `\r`, `\t`, `\uXXXX` respectively — this matches both Python and Rust defaults.

The test suite (`tests/test_non_ascii.py`) verifies byte-exactness across runs with payloads containing Latin-1 accents, Chinese, Japanese, Korean, Arabic, Cyrillic, emoji, and math symbols. The pin is also verified by `test_payload_hash_uses_raw_utf8_bytes`, which asserts that the `ensure_ascii=True` form would produce a *different* hash — pinning the policy against silent regression.

---

## Agreement (v0.2)

v0.1 recorded and verified; it could not agree. This section is the agreement layer. It adds **no new stitch and no new entry fields** — agreement is built entirely from the existing wire format, which is why it can stay this small.

*Why no twelfth stitch:* an exchange booking is a fact about a cell-shaped record — "tenant T records that an exchange happened, with this counterpart, over this payload" — and `BIND` already records exactly that shape. A dedicated `EXCHANGE` opcode would duplicate `BIND`'s fields, force every implementation to grow a second code path, and buy nothing the descriptor below doesn't already say. The stitch count stays at 11, honestly: agreement is a *reading discipline* over bookings, not a new write primitive.

### Exchange bookings

An exchange between two tenants is booked **by each side into its own ledger**, as a `BIND` entry with `kind` = `"exchange"` and `payload` = the descriptor:

```json
{"peer": "<counterpart tenant identity>", "ph": "<payload hash>", "side": "send", "xchg": "<exchange id>"}
```

- `xchg` — an exchange identifier, chosen by the participants (any string; unique per pair by convention, like a reference number on a cheque).
- `side` — `"send"` in the sender's ledger, `"receive"` in the receiver's.
- `peer` — the counterpart's identity: the receiver in the sender's booking, the sender in the receiver's.
- `ph` — the payload hash: SHA-256 (hex) of the canonical JSON of the exchanged payload, `json.dumps(payload, sort_keys=True, separators=(",", ":"))`. The payload itself is NOT recorded — the hash is the commitment both sides must match. Booking costs no γ.

The Python helpers are `book_send` / `book_receive`; the descriptor format above is the normative part, not the helpers.

### Audit

A foreign ledger is audited from a local copy, producing a structured verdict: entry count, chain ok or the index of the first broken entry, law ok or the maximum excess over budget, the head hash (hash of the last entry of the valid prefix), and the tenant identity found. The law replay is the one from the v0.1 cross-instance exercise, now library API (`audit_ledger`): γ accumulates each `EFFECT`'s recorded `s` per tenant, resets on `TICK`, and every entry's recorded `g` must equal the replayed running sum and stay within budget. Timestamps are never consulted. If the chain breaks at entry k, the audit's statements cover the valid prefix 0..k−1 only. The audit assumes a per-tenant budget (default `log₂3`); the assumed budget is part of the verdict, because v0.2 ledgers do not record configured budgets. (v0.3 removes the assumption where a tenant declares a budget in-ledger — see "Tenant budget declarations".)

### Minting

Given two ledger copies and an exchange id, the agreement operation:

1. Audits BOTH ledgers. A broken chain or a law violation on either side refuses everything.
2. Finds a SEND booking for the exchange in either ledger; the booking's `ns` is the sender, its `peer` the intended receiver, and the *other* ledger is the counterpart's.
3. Finds a RECEIVE booking for the same exchange id in the counterpart's ledger.
4. Mints **only if** the mirror agrees exactly: the receive booking's `ns` equals the sender's `peer`, its `peer` equals the sender, and its `ph` equals the send booking's `ph`.

The minted shared state is the Agreement record itself — `{xchg, sender, receiver, payload_hash}` plus the pins below. It is a derived value: nothing is written to either ledger, and any holder of the two files can re-derive it at any time. The fold of a pair of ledgers (`agreed_state`) is every exchange id booked in either, partitioned into minted and refused.

Every failure is a distinct verdict, never a silent partial fold:

| Reason | Meaning |
|---|---|
| `agreed` | Mirror bookings agree; the exchange is shared state. |
| `no_send` | No SEND booking for this exchange in either ledger. |
| `missing_mirror` | A SEND was booked; the counterpart ledger has no RECEIVE for it. |
| `peer_mismatch` | A RECEIVE exists, but it names different parties than the SEND. |
| `payload_mismatch` | The RECEIVE names the right parties but a different payload hash — the two sides recorded different exchanges under one id. |
| `chain_broken` | One ledger's hash chain fails (detail names the file and entry index). |
| `law_violated` | One ledger's replay breaks the conservation law (detail names the file and the violation). |

### The head-hash pin (anchor)

An Agreement pins both ledgers at agreement time: head hash and entry count per side. A later re-audit (`verify_anchor`) compares a ledger against its pin:

- `anchored` — the pinned head is intact at the pinned position. Appends since the pin are fine; the pin is a prefix.
- `truncated` — fewer entries than pinned, prefix intact: the tail was cut after the pin. **This closes v0.1's tail-truncation blindness for any exchange that was agreed before the cut** — a fresh fold of a truncated pair can still mint (the mirror may survive), but its head differs from the pin, and the pin is what catches it.
- `rewritten` — the entry at the pinned position carries a different hash.
- `chain_broken` — the chain fails inside the pinned prefix.
- `missing` — the file is gone.

Residual limit, honestly: the anchor is only as trustworthy as the pin. A holder must keep the Agreement record itself intact; an attacker who can replace both the ledger AND your stored pin defeats it, as with any hash anchor not witnessed elsewhere. FNV-1a is a tamper-evident canary, not a cryptographic hash — the pin detects truncation and rewriting relative to what you kept, not a forgery crafted against FNV-1a's weaknesses by someone who also controls the pin.

### What agreement does NOT prove

Agreement proves the two *records* agree — that both sides booked the same exchange over the same payload hash, in ledgers that pass audit. It does not prove a ledger copy is authentic (identity is a string claim in v0.2; see Tenant identity), that the payload ever moved, or that the payload hash corresponds to any particular real-world thing. Those are higher layers' problems, deliberately.

---

## Tenant budget declarations (v0.3)

v0.2's audit had to assume each tenant's budget (default log₂3) because budgets were configured in-process, not recorded in the ledger. v0.3 closes that gap.

A tenant records their per-tick budget C with the `declare_budget(tenant, ledger, budget=None)` helper. This is **not a new stitch** — it's a `bind` with `kind = "tenant"` and `payload = {"budget": <float>}`. The 11-stitch count is unchanged.

### Wire format

A budget declaration is a `BIND` entry where:
- `kind` = `"tenant"`
- `payload` = `{"budget": <positive float>}`

The audit reads per-tenant budgets from these declarations **as it replays**: a tenant's first declaration takes effect at its own position in the ledger and governs entries from there on. Entries before it are judged against the budget then in force (a prior declaration, or the audit's `budget` parameter fallback). Later declarations by the same tenant are recorded but ignored (to change a budget, start a new ledger). Position-awareness closes a real hole: with position-blind extraction, an overspend followed by a larger declaration would audit clean — it does not. Tenants without an in-ledger declaration fall back to the audit's `budget` parameter (default `log₂3`) — preserves backward compatibility with v0.2 ledgers.

### Audit verdict

The `AuditVerdict` dataclass gains a `budgets: dict` field mapping tenant identity strings to their declared budgets. The existing `budget: float` field remains as the fallback for tenants without declarations (default `log₂3`).

### Example

```python
import stitch
alice = stitch.Tenant(identity="alice")
ledger = stitch.Ledger(path="./alice.jsonl")

# Declare a custom budget of 3.0 bits per tick
stitch.declare_budget(alice, ledger, budget=3.0)

# Now the audit will use 3.0 instead of the default log₂3
verdict = stitch.audit_ledger("./alice.jsonl")
assert verdict.budgets == {"alice": 3.0}
```

---

## N-party agreement (v0.3)

v0.2's minting was bilateral — only two ledgers. v0.3 generalises: an exchange mints across N ledgers (N ≥ 2) when every SEND booked in any ledger has a mirror RECEIVE in some OTHER ledger, all bookings agree on the payload hash, and every ledger has at least one booking.

For N=2 this is exactly `agree` — a 2-cycle. For N>2 the typical ring pattern has each party booking a SEND to their successor and a RECEIVE from their predecessor in the ring.

### API

- `agree_n(paths: list[PathLike], xchg: str, budget: float = LOG2_3) -> AgreementN`
- `agreed_state_n(paths: list[PathLike], budget: float = LOG2_3) -> (list[AgreementN], list[AgreementN])`

The `AgreementN` dataclass mirrors the bilateral `Agreement` but with `heads: list[str]` and `entries_list: list[int]` (parallel arrays indexed the same as the input `paths`) instead of `head_a`/`head_b`/`entries_a`/`entries_b`. The `parties: list[str]` field lists all distinct tenant identities that booked.

### Refusal reasons

The same reason codes as bilateral `agree` apply: `NO_SEND`, `MISSING_MIRROR`, `PAYLOAD_MISMATCH`, `PEER_MISMATCH`, `CHAIN_BROKEN`, `LAW_VIOLATED`. No new reason codes were added — the mirror rule generalises cleanly.

### Backward compatibility

Bilateral `agree()` and `agreed_state()` are unchanged. `agree_n()` with `len(paths) == 2` produces the same verdict as `agree(paths[0], paths[1], xchg)` (verified in tests).

---

## Ed25519 signatures (v0.3)

v0.2's `attest` stitch used a SHA-256 hash placeholder for the signature. v0.3 adds real Ed25519 (RFC 8032) signatures, implemented in pure Python with zero external dependencies (uses only `hashlib.sha512` from the stdlib), and wires them into `attest`/`audit_ledger` as an opt-in (see below). What it does not do is bind keys to identities: which public key belongs to which tenant is still established out of band, so copy authenticity is enforced only where a writer signs and an auditor supplies keys — the gap is narrowed and honestly bounded, not "closed".

### The `stitch.crypto` subpackage

Located at `stitch-python/stitch/crypto/`. **Opt-in** — nothing imports it at module load. The core imports it lazily on exactly two paths: `attest(..., secret_key=...)` and `audit_ledger(..., attest_keys=...)`. Users who never pass keys never trigger the import, and the zero-dependency rule for the core is preserved.

### API

```python
from stitch.crypto import Key, sign, verify

key = Key.generate()           # new random Ed25519 keypair
sig = key.sign(message)        # 64-byte signature
key.verify(message, sig)       # True/False

# Or as bare functions:
from stitch.crypto import secret_key_bytes, public_key
sk = secret_key_bytes()        # 32 random bytes
pk = public_key(sk)            # 32-byte public key
sig = sign(sk, message)        # 64-byte signature
verify(pk, message, sig)       # True/False
```

### Tenant identity as public key

A `Key`'s `identity` attribute is the 32-byte public key as a 64-character lowercase hex string. Using `key.identity` as the `Tenant.identity` means the ledger's `ns` field carries the public key — anyone with the public key can verify signatures on entries from this tenant.

```python
import stitch
from stitch.crypto import Key

key = Key.generate()
alice = stitch.Tenant(identity=key.identity)  # 64-char hex public key
```

### Wiring into attest and audit (opt-in)

Two narrow hooks, both default-off and byte-identical when unused:

- `attest(tenant, ledger, claim, cell="", secret_key=None)` — with a 32-byte secret key, the entry's `sig` field is a real Ed25519 signature (128 lowercase hex chars) over `"<tenant>:<cell>:<claim>"`. Without a key, `sig` is the v0.2 SHA-256 placeholder, unchanged.
- `audit_ledger(path, budget=..., attest_keys=None)` — `attest_keys` maps tenant identity → public key (32 raw bytes or 64-char hex). For each keyed tenant, every ATTEST entry is checked: a real signature that verifies counts in `attest_verified`; a placeholder where a key was supplied counts in `attest_unsigned`; a malformed or non-verifying signature counts in `attest_failures`. The verdict property `signatures_ok` is True exactly when there are no failures and no unsigned attestations among keyed tenants. `AuditVerdict.ok` keeps its v0.2 meaning (chain + conservation law); callers wanting authenticity require `verdict.signatures_ok` as well. Only ATTEST entries can be signed — agreement bookings are not signed in v0.3.

### Verification status

Verified during adoption, not merely round-tripped: the RFC 8032 §7.1 vectors (TEST 1 and TEST 2) are pinned in `tests/test_rfc8032_vectors.py` and pass byte-for-byte (public-key derivation and signature), and the implementation was cross-checked against OpenSSL in both directions (see VERIFICATION.md). Recorded soft spots: `verify` does not screen for small-order points (the cofactored check still rejects signatures that do not satisfy the verification equation), and the module is slow (~270 ms per sign/verify on a small VM) — fine at ledger scale, not a bulk-signing tool.

---

## Implementation status

| Language | Status | Notes |
|---|---|---|
| Python | ✅ Reference | `stitch-python/` — full implementation incl. the v0.2 agreement layer, tests pass |
| Rust | 🚧 Partial | `stitch-rs/` — core types + `bind` + the v0.2 audit; agreement/minting is Python-first, not yet ported |
| TypeScript | 🚧 Planned | roadmap |
| C99 | 🚧 Planned | roadmap |

---

## Changelog

- **v0.1** (2026-10-26): Initial public spec. 11 stitches, FNV-1a 64-bit hash chain, discrete KL surprise oracle. Python reference implementation. Rust skeleton.
- **v0.2** (2026-10-26): The agreement layer. Exchange bookings via `BIND` kind `"exchange"` (no new stitch), foreign-ledger audit as library API, bilateral minting with distinct refusal verdicts, and the head-hash anchor against tail-truncation. Audit also ported to Rust; minting is Python-first. Ed25519 identity and the non-ASCII encoding pin, previously sketched for this version, are deferred (see Tenant identity and the byte-exactness limitation) — the zero-dependency rule and the agreement gap outranked them.
- **v0.3** (2026-10-26): Drafted by Casey's Z.ai (GLM) on the published v0.2; assessed and adopted by Muse with fixes. **R1** — non-ASCII encoding pin (closes the byte-exactness gap; Python `ensure_ascii=False`, Rust unchanged; one hard break: v0.2-era non-ASCII artefacts do not verify under the pin — ASCII is fully bidirectional). **R2** — tenant budget declarations via `declare_budget()` (closes the audit-assumption gap), **with an adoption patch making declarations position-aware** — the draft's audit let a declaration appended after an overspend excuse it; the patched audit judges each entry against the budget in force at its position, and a regression test pins the refusal. **R3** — N-party agreement via `agree_n`/`agreed_state_n` returning `AgreementN` (closes the bilateral-only gap; every SEND must have a mirror RECEIVE in some other ledger, for any N≥2; a party is any ledger carrying a booking). **R4** — pure-Python Ed25519 (RFC 8032) in the `stitch.crypto` subpackage (stdlib-only via `hashlib.sha512`), **wired in opt-in** on adoption: `attest` signs when given a secret key, `audit_ledger` verifies when given public keys; defaults byte-identical to v0.2. RFC 8032 §7.1 vectors pinned as tests. Key-to-identity binding remains out of band — copy authenticity is narrowed, not closed. Test suite grows from 54 to 150. The stitch count stays at 11. The v0.3 features are Python-first; the Rust crate is unchanged from v0.2.
- **v0.4** (planned): C99 implementation, cross-language test harness, FFI bindings; TypeScript port.
- **v0.5** (planned): Pluggable surprise oracles (KL, hex lattice, SVGD).
- **v0.6** (planned): Multi-tenant budget pool (shared C, per-tenant quotas).

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
