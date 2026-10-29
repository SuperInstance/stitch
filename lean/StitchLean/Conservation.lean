/-
  StitchLean/Conservation.lean — the conservation law, proved for
  the write path.

  The writer is modelled as a state machine whose state is the
  auditor's replay state (γ per tenant, declarations in force) and
  whose transitions are exactly the accepted writes of the Python
  reference:

  * EFFECT (`stitches.py:effect` + `core.py:Tenant.can_afford`):
    the oracle's surprise `s` is charged iff `γ + s ≤ C`; the entry
    records `s` and the new `g = γ + s`. (The model requires
    `s ≥ 0`: SPEC calls γ "cumulative surprise dissipated (a
    non-negative float)", and the default oracle returns counts of
    mismatched/new keys — never negative. The code's float epsilon
    in `can_afford` is not modelled; see README.)
  * TICK: γ resets to 0 for the tenant; the entry records `g = 0`.
  * Bookings, budget declarations, and all other stitches are free:
    γ unchanged, the entry records the current `g`
    (`book_send`/`book_receive`: "Booking costs no γ").

  Writer and auditor share the transition function `advance` — the
  writer *chooses* entries satisfying the acceptance and recording
  discipline above, the auditor *checks* them — so the bridge
  theorem below is a statement about the discipline, not about two
  implementations agreeing by accident.

  One hypothesis deserves its name in lights: `DeclOK`. SPEC's
  "Two knobs" paragraph warns that write-time enforcement
  (`Tenant(budget=C)`) and the in-ledger declaration
  (`declare_budget`) are separate, and instructs setting both to
  the same C. The bridge theorem needs exactly that: if a tenant
  declared a *smaller* budget than the writer enforces, the writer
  could lawfully (by its own knob) write entries the auditor then
  refuses. `DeclOK` — every declaration in force declares C — is
  that instruction, formalized. The writer below only ever declares
  C, and declarations are the only way `declared` changes, so
  `DeclOK` is preserved by every step.
-/

import StitchLean.Audit

namespace Stitch

/-- The conservation invariant on a state: every tenant's γ this
    tick lies in `[0, C]`. With η := C − γ this is SPEC's
    `γ + η ≤ C` with η non-negative — the same statement, since
    η is *defined* as the remaining headroom. -/
def Inv (C : Int) (st : ReplayState) : Prop :=
  ∀ t, 0 ≤ st.gamma t ∧ st.gamma t ≤ C

/-- Declaration hygiene: every budget declaration in force declares
    exactly the enforced budget `C` (SPEC's two-knobs rule — see the
    module docstring). -/
def DeclOK (C : Int) (st : ReplayState) : Prop :=
  ∀ t, st.declared t = none ∨ st.declared t = some C

/-- Under declaration hygiene, the budget in force for every tenant
    is `C`, declared or not. -/
theorem budgetOf_of_declOK {C : Int} {st : ReplayState} (h : DeclOK C st)
    (t : TenantId) : budgetOf C st t = C := by
  cases h' : st.declared t with
  | none =>
      simp [budgetOf, Option.getD, h']
  | some v =>
      have hv : v = C := by
        rcases h t with h1 | h1
        · rw [h'] at h1
          cases h1
        · rw [h'] at h1
          exact Option.some.inj h1
      simp [budgetOf, Option.getD, h', hv]

/-- One accepted write: from state `st`, entry `e` is appended and
    the state advances exactly as the auditor's replay will advance
    it. The constructors are the write rules of the reference
    implementation (see the module docstring). A *rejected* EFFECT
    appends nothing and changes nothing, so it has no constructor:
    traces contain only accepted writes, as ledgers do. -/
inductive WStep (C : Int) : ReplayState → Entry → ReplayState → Prop where
  | effect {st : ReplayState} {ns : TenantId} {s : Int}
      (hns : 0 ≤ s) (afford : st.gamma ns + s ≤ C) :
      WStep C st (Entry.effect ns s (st.gamma ns + s))
        (advance st (Entry.effect ns s (st.gamma ns + s)))
  | tick {st : ReplayState} {ns : TenantId} :
      WStep C st (Entry.tick ns) (advance st (Entry.tick ns))
  | booking {st : ReplayState} {b : Booking} :
      WStep C st (Entry.booking b (st.gamma b.tenant))
        (advance st (Entry.booking b (st.gamma b.tenant)))
  | declareBudget {st : ReplayState} {ns : TenantId} :
      WStep C st (Entry.declareBudget ns C (st.gamma ns))
        (advance st (Entry.declareBudget ns C (st.gamma ns)))
  | other {st : ReplayState} {ns : TenantId} :
      WStep C st (Entry.other ns (st.gamma ns))
        (advance st (Entry.other ns (st.gamma ns)))

/-- A single accepted write preserves the conservation invariant:
    the only γ-changing writes are EFFECT, which is accepted only
    within budget and only adds non-negative surprise, and TICK,
    which resets to 0 — and `0 ≤ C` is why the budget must be
    positive. -/
theorem WStep.preserves_inv {C : Int} (hC : 0 < C) {st st' : ReplayState}
    {e : Entry} (h : WStep C st e st') (hinv : Inv C st) : Inv C st' := by
  cases h with
  | effect hns afford =>
      rename_i ns s
      intro t
      have hγ := hinv ns
      rw [advance_gamma_effect]
      by_cases ht : t = ns
      · subst ht
        rw [upd_self]
        exact ⟨by omega, afford⟩
      · rw [upd_of_ne ht]
        exact hinv t
  | tick =>
      rename_i ns
      intro t
      rw [advance_gamma_tick]
      by_cases ht : t = ns
      · subst ht
        rw [upd_self]
        exact ⟨by omega, by omega⟩
      · rw [upd_of_ne ht]
        exact hinv t
  | booking =>
      rename_i b
      intro t
      rw [advance_gamma_booking]
      exact hinv t
  | declareBudget =>
      rename_i ns
      intro t
      rw [advance_gamma_declare]
      exact hinv t
  | other =>
      rename_i ns
      intro t
      rw [advance_gamma_other]
      exact hinv t

/-- A single accepted write preserves declaration hygiene: the
    only declaration-changing write declares exactly `C`. -/
theorem WStep.preserves_declOK {C : Int} (_hC : 0 < C) {st st' : ReplayState}
    {e : Entry} (h : WStep C st e st') (hdecl : DeclOK C st) :
    DeclOK C st' := by
  cases h with
  | effect _ _ =>
      rename_i ns s
      intro t
      rw [advance_declared_effect]
      exact hdecl t
  | tick =>
      rename_i ns
      intro t
      rw [advance_declared_tick]
      exact hdecl t
  | booking =>
      rename_i b
      intro t
      rw [advance_declared_booking]
      exact hdecl t
  | other =>
      rename_i ns
      intro t
      rw [advance_declared_other]
      exact hdecl t
  | declareBudget =>
      rename_i ns
      intro t
      rw [advance_declared_declare]
      by_cases ht : t = ns
      · subst ht
        by_cases hc : st.declared t = none ∧ 0 < C
        · rw [if_pos hc, upd_self]
          exact Or.inr rfl
        · rw [if_neg hc]
          exact hdecl t
      · by_cases hc : st.declared ns = none ∧ 0 < C
        · rw [if_pos hc, upd_of_ne ht]
          exact hdecl t
        · rw [if_neg hc]
          exact hdecl t

/-- A ledger written by a sequence of accepted writes: the trace
    of a writer. The final state is the writer's state after the
    last entry. -/
inductive WTrace (C : Int) : ReplayState → Ledger → ReplayState → Prop where
  | nil {st : ReplayState} : WTrace C st [] st
  | cons {st st' st'' : ReplayState} {e : Entry} {l : Ledger}
      (step : WStep C st e st') (rest : WTrace C st' l st'') :
      WTrace C st (e :: l) st''

/-- **Conservation.** Any sequence of accepted writes preserves the
    invariant: at every point of the trace — in particular after
    every entry of the written ledger — every tenant's γ satisfies
    `0 ≤ γ ≤ C`, i.e. `γ + η ≤ C` per tenant per tick. This is the
    integer skeleton of SPEC's conservation law, proved for an
    arbitrary positive budget `C` (the proof never uses the value
    log₂3, only that acceptance checks `γ + s ≤ C`). -/
theorem WTrace.conservation {C : Int} (hC : 0 < C) {st st' : ReplayState}
    {l : Ledger} (h : WTrace C st l st') : Inv C st → Inv C st' := by
  induction h with
  | nil => intro hinv; exact hinv
  | cons hs _ ih => intro hinv; exact ih (hs.preserves_inv hC hinv)

/-- **The writer passes its own audit.** A ledger produced by
    accepted writes, from a state satisfying the invariant and
    declaration hygiene, is `Lawful`: replaying it entry by entry,
    every recorded `g` equals the replayed γ and every replayed γ
    is within the budget in force. In words: an honest writer's
    ledger always passes the conservation half of a foreign audit —
    which is exactly what `agree` demands before it will mint. -/
theorem WTrace.lawful {C : Int} (hC : 0 < C) {st st' : ReplayState}
    {l : Ledger} (h : WTrace C st l st') :
    Inv C st → DeclOK C st → Lawful C st l := by
  induction h with
  | nil => intro _ _; exact Lawful.nil _
  | cons hs htr ih =>
      intro hinv hdecl
      have hinv' := hs.preserves_inv hC hinv
      have hdecl' := hs.preserves_declOK hC hdecl
      have htail := ih hinv' hdecl'
      cases hs with
      | effect hns afford =>
          rename_i ns s
          refine Lawful.cons ?_ ?_ htail
          · simp only [Entry.g, Entry.ns, advance_gamma_effect, upd_self]
          · simp only [Entry.ns, advance_gamma_effect, upd_self]
            rw [budgetOf_of_declOK hdecl']
            exact afford
      | tick =>
          rename_i ns
          refine Lawful.cons ?_ ?_ htail
          · simp only [Entry.g, Entry.ns, advance_gamma_tick, upd_self]
          · simp only [Entry.ns, advance_gamma_tick, upd_self]
            rw [budgetOf_of_declOK hdecl']
            omega
      | booking =>
          rename_i b
          refine Lawful.cons ?_ ?_ htail
          · simp only [Entry.g, Entry.ns, advance_gamma_booking]
          · simp only [Entry.ns, advance_gamma_booking]
            rw [budgetOf_of_declOK hdecl']
            exact (hinv b.tenant).2
      | declareBudget =>
          rename_i ns
          refine Lawful.cons ?_ ?_ htail
          · simp only [Entry.g, Entry.ns, advance_gamma_declare]
          · simp only [Entry.ns, advance_gamma_declare]
            rw [budgetOf_of_declOK hdecl']
            exact (hinv ns).2
      | other =>
          rename_i ns
          refine Lawful.cons ?_ ?_ htail
          · simp only [Entry.g, Entry.ns, advance_gamma_other]
          · simp only [Entry.ns, advance_gamma_other]
            rw [budgetOf_of_declOK hdecl']
            exact (hinv ns).2

/-- The initial state satisfies the invariant (every γ is 0, and
    `0 ≤ C`). -/
theorem Inv_init {C : Int} (hC : 0 < C) : Inv C ReplayState.init := by
  intro t
  show (0 : Int) ≤ 0 ∧ (0 : Int) ≤ C
  exact ⟨by omega, by omega⟩

/-- The initial state satisfies declaration hygiene (nothing is
    declared). -/
theorem DeclOK_init {C : Int} : DeclOK C ReplayState.init := by
  intro t
  exact Or.inl rfl

/-- Headline form of conservation: a ledger written from the
    initial state keeps every tenant's final γ within budget. -/
theorem conservation_of_written {C : Int} (hC : 0 < C) {st' : ReplayState}
    {l : Ledger} (h : WTrace C ReplayState.init l st') : Inv C st' :=
  h.conservation hC (Inv_init hC)

/-- Headline form of the bridge: a ledger written from the initial
    state passes the conservation-law audit, in full — every
    recorded `g` replays exactly and no entry exceeds the budget in
    force at its position. -/
theorem lawful_of_written {C : Int} (hC : 0 < C) {st' : ReplayState}
    {l : Ledger} (h : WTrace C ReplayState.init l st') : LawfulLedger C l :=
  h.lawful hC (Inv_init hC) DeclOK_init

end Stitch
