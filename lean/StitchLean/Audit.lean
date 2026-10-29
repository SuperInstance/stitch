/-
  StitchLean/Audit.lean — the foreign-ledger audit, abstracted.

  Two halves, matching `agreement.py:_audit`:

  1. The hash chain: every entry's stored `h` must equal the hash
     recomputed from the entry's content seeded by the previous
     entry's hash. Modelled with an abstract hash function (below).

  2. The conservation-law replay: γ accumulates each EFFECT's
     recorded `s` per tenant, resets on TICK, every entry's recorded
     `g` must equal the replayed γ, and the replayed γ must stay
     within the budget in force at that entry's position (in-ledger
     declarations are position-aware, first declaration wins).
-/

import StitchLean.Model

namespace Stitch

/-- Point update of a tenant-indexed map. (Lean core 4.20's shipped
    libraries do not export `Function.update` under that name; this
    is the same definition, kept local so the model's dependency
    surface is the prelude plus this file.) -/
def upd {α : Type} (f : TenantId → α) (t : TenantId) (v : α) : TenantId → α :=
  fun u => if u = t then v else f u

theorem upd_self {α : Type} (f : TenantId → α) (t : TenantId) (v : α) :
    upd f t v t = v :=
  if_pos rfl

theorem upd_of_ne {α : Type} {t u : TenantId} (h : u ≠ t) (f : TenantId → α)
    (v : α) : upd f t v u = f u :=
  if_neg h

/-! ## The hash chain, abstracted

SPEC hashes each entry with FNV-1a 64-bit, seeded by the previous
entry's hash, over the entry's canonical JSON. The formalization
does not model FNV-1a or JSON: `H` is an abstract function from
(previous hash, entry) to hash, and `seed` is the genesis value
(SPEC's FNV_OFFSET). What tamper-evidence means for such a chain
rests on a cryptographic property of `H`, stated below as an
explicit hypothesis rather than proved — as the Python code assumes,
without stating, that recomputing hashes detects edits.

HONESTY NOTE: the real FNV-1a does NOT satisfy second-preimage
resistance; SPEC itself calls it "a tamper-evident canary, not a
cryptographic hash". Every conclusion that depends on `H`'s
hypothesis is therefore conditional on an idealized hash. The two
headline theorems do not depend on it: minting soundness quantifies
over given ledgers (their chains enter only through `ChainValid`,
an opaque audit gate, exactly as `agree` consumes `chain_ok` as a
boolean), and conservation never mentions hashes at all. -/

section HashChain

variable {Hash : Type} (H : Hash → Entry → Hash) (seed : Hash)

/-- The hashes a ledger's contents compute to, in order, starting
    from running hash `h`. (`_audit` recomputes each entry's `h`
    from the running seed and compares with the stored value.) -/
def chainHashesFrom (h : Hash) : Ledger → List Hash
  | [] => []
  | e :: rest => H h e :: chainHashesFrom (H h e) rest

/-- The computed chain for a whole ledger, from the genesis seed. -/
def chainHashes (l : Ledger) : List Hash := chainHashesFrom H seed l

/-- The chain audit: the stored hashes are exactly the computed
    ones. (The Python audit's valid-prefix subtlety — its statements
    cover only the entries before the first break — is modelled by
    applying `ChainValid` and `Lawful` to a prefix ledger; `agree`
    itself only consumes the whole-ledger booleans.) -/
def ChainValid (stored : List Hash) (l : Ledger) : Prop :=
  stored = chainHashes H seed l

/- The cryptographic hypothesis, stated once: `H` is
    second-preimage resistant in its entry argument — given a
    previous hash and an entry, no *different* entry hashes to the
    same value under the same previous hash. Idealized here to
    injectivity in the entry argument, the standard formalization
    shortcut. NOT a property of FNV-1a (see the note above). -/

/-- Tamper evidence at one position: two ledgers whose next entries
    differ cannot have equal hash chains from the same previous
    hash. This is the one place the hypothesis above is spent. -/
theorem chainHashesFrom_ne_of_head_ne
    (H_secondPreimage : ∀ (p : Hash) (a b : Entry), H p a = H p b → a = b)
    {p : Hash} {a b : Entry} (hab : a ≠ b)
    (rest₁ rest₂ : Ledger) :
    chainHashesFrom H p (a :: rest₁) ≠ chainHashesFrom H p (b :: rest₂) := by
  intro hEq
  apply hab
  apply H_secondPreimage p
  have h1 : chainHashesFrom H p (a :: rest₁)
      = H p a :: chainHashesFrom H (H p a) rest₁ := rfl
  have h2 : chainHashesFrom H p (b :: rest₂)
      = H p b :: chainHashesFrom H (H p b) rest₂ := rfl
  rw [h1, h2, List.cons.injEq] at hEq
  exact hEq.1

end HashChain

/-! ## The conservation-law replay -/

/-- State of the auditor's replay: per-tenant running γ, and the
    per-tenant in-ledger budget declarations in force (first
    declaration wins — see `advance`). -/
structure ReplayState where
  gamma : TenantId → Int
  declared : TenantId → Option Int

/-- The auditor's starting state: every γ is 0, no declarations. -/
def ReplayState.init : ReplayState := ⟨fun _ => 0, fun _ => none⟩

/-- The budget in force for tenant `t`: its declaration if one has
    taken effect, else the audit's fallback budget `C` (SPEC's
    `budget` parameter, default log₂3). -/
def budgetOf (C : Int) (st : ReplayState) (t : TenantId) : Int :=
  (st.declared t).getD C

/-- One step of the replay, in the auditor's exact order
    (`agreement.py:_audit`):

    1. If the entry is a *first* budget declaration by its tenant
       (a `kind = "tenant"` BIND whose payload budget is positive,
       and the tenant has no declaration yet), the declaration takes
       effect *at this entry's own position* — it governs this
       entry's budget check and all later ones.
    2. γ advances for the entry's tenant: EFFECT adds its recorded
       `s`; TICK resets to 0; anything else leaves γ unchanged.

    The auditor then checks the entry's recorded `g` against the
    advanced γ and the advanced γ against the budget in force —
    that check is `Lawful`, below. -/
def advance (st : ReplayState) (e : Entry) : ReplayState :=
  let declared' :=
    match e with
    | .declareBudget ns budget _ =>
        if st.declared ns = none ∧ 0 < budget then
          upd st.declared ns (some budget)
        else st.declared
    | _ => st.declared
  let gamma' :=
    match e with
    | .effect ns s _ => upd st.gamma ns (st.gamma ns + s)
    | .tick ns => upd st.gamma ns 0
    | _ => st.gamma
  ⟨gamma', declared'⟩

/-- The audit's conservation-law verdict, as an inductive
    predicate over a ledger replayed from state `st`: every entry's
    recorded `g` equals the replayed γ of its tenant after the
    entry, and that γ is within the budget in force after the entry
    (a declaration at the entry's own position included).
    Python compares floats with tolerances (`1e-6` for the `g`
    check, an epsilon for the budget check); the model compares
    integers exactly — see README "Simplifications". -/
inductive Lawful (C : Int) : ReplayState → Ledger → Prop where
  | nil (st : ReplayState) : Lawful C st []
  | cons {st : ReplayState} {e : Entry} {l : Ledger}
      (hg : e.g = (advance st e).gamma e.ns)
      (hbudget : (advance st e).gamma e.ns ≤ budgetOf C (advance st e) e.ns)
      (rest : Lawful C (advance st e) l) :
      Lawful C st (e :: l)

/-- A ledger passes the law audit from the initial state. -/
def LawfulLedger (C : Int) (l : Ledger) : Prop :=
  Lawful C ReplayState.init l

/-! ## Projection lemmas for `advance`

The conservation proof (Conservation.lean) works one entry at a
time; these are the definitional facts it reuses — what each entry
shape does to each half of the replay state. -/

theorem advance_gamma_effect (st : ReplayState) (ns : TenantId) (s g : Int) :
    (advance st (Entry.effect ns s g)).gamma
      = upd st.gamma ns (st.gamma ns + s) := rfl

theorem advance_declared_effect (st : ReplayState) (ns : TenantId) (s g : Int) :
    (advance st (Entry.effect ns s g)).declared = st.declared := rfl

theorem advance_gamma_tick (st : ReplayState) (ns : TenantId) :
    (advance st (Entry.tick ns)).gamma = upd st.gamma ns 0 := rfl

theorem advance_declared_tick (st : ReplayState) (ns : TenantId) :
    (advance st (Entry.tick ns)).declared = st.declared := rfl

theorem advance_gamma_booking (st : ReplayState) (b : Booking) (g : Int) :
    (advance st (Entry.booking b g)).gamma = st.gamma := rfl

theorem advance_declared_booking (st : ReplayState) (b : Booking) (g : Int) :
    (advance st (Entry.booking b g)).declared = st.declared := rfl

theorem advance_gamma_declare (st : ReplayState) (ns : TenantId)
    (budget g : Int) :
    (advance st (Entry.declareBudget ns budget g)).gamma = st.gamma := rfl

theorem advance_declared_declare (st : ReplayState) (ns : TenantId)
    (budget g : Int) :
    (advance st (Entry.declareBudget ns budget g)).declared
      = if st.declared ns = none ∧ 0 < budget then
          upd st.declared ns (some budget)
        else st.declared := rfl

theorem advance_gamma_other (st : ReplayState) (ns : TenantId) (g : Int) :
    (advance st (Entry.other ns g)).gamma = st.gamma := rfl

theorem advance_declared_other (st : ReplayState) (ns : TenantId) (g : Int) :
    (advance st (Entry.other ns g)).declared = st.declared := rfl

end Stitch
