/-
  StitchLean/Model.lean — the ledger, abstracted.

  This module defines the data the SPEC's audit and agreement rules
  actually read, and nothing else.

  Mapping to SPEC.md / stitch-python:

  * A ledger entry in the Python reference is a JSON object with
    common fields `t` (ns timestamp), `ns` (tenant), `op` (one of 11
    stitch names), `g` (tenant γ after the op), `h` (chain hash),
    and per-stitch extra fields. The two rules formalized in this
    project — the conservation-law replay in `agreement.py:_audit`
    and the mirror rule in `agreement.py:agree` — read only: `ns`,
    `op`, for EFFECT the recorded `s`, for every entry the recorded
    `g`, for BIND the `kind` and `payload` (exchange descriptors and
    budget declarations), and the hash chain. Timestamps are never
    consulted (SPEC: "Timestamps are never consulted"), cell ids are
    never compared across ledgers, and payloads are read only
    through the two descriptor shapes. `Entry` below keeps exactly
    the read fields and erases the rest; see README
    "Simplifications".

  * `Entry.booking` is a `BIND` entry with `kind = "exchange"`; its
    `Booking` is the descriptor `{peer, ph, side, xchg}` plus the
    entry's `ns` (the booking tenant), which `_bookings` returns
    alongside the descriptor.

  * `Entry.declareBudget` is a `BIND` entry with `kind = "tenant"`
    and payload `{"budget": b}`. (The replay, not the entry shape,
    enforces SPEC's `b > 0` and first-declaration-wins rules — see
    Audit.lean `advance`.)

  * `Entry.tick` records `g = 0` by construction: `stitches.py:tick`
    writes `"g": 0.0` unconditionally, so the model bakes it in
    rather than carrying a field that could lie.

  * `Entry.other` collapses every remaining stitch (BIND of other
    kinds, LINK, ATTEST, DELEGATE, CONTEST, MERGER, REVOKE,
    WITHDRAW; VIEW writes nothing). The audit and the conservation
    law treat all of them identically — γ unchanged, recorded `g`
    still checked — so one constructor is faithful for the two
    theorems proved here, and only for them.

  * Amounts (γ, `s`, budgets) are integers. This is the integer
    skeleton of the law, as the README's "Simplifications" section
    explains in full: the conservation argument is pure
    linearly-ordered additive reasoning, and Lean 4.20's shipped
    libraries carry no public ordered-field API for `Rat` (it is an
    internal `Std` type there) while Mathlib does not fit this
    box's disk.
-/

namespace Stitch

/-- Tenant identity: an arbitrary string (SPEC: "an identity is a
    claim, not a proof"). -/
abbrev TenantId := String

/-- Exchange identifier: an arbitrary string, chosen by the
    participants (SPEC: like a reference number on a cheque). -/
abbrev XchgId := String

/-- Payload hash: the hex SHA-256 of the canonical JSON of the
    exchanged payload. The model treats it as an opaque string —
    agreement compares hashes for equality, never inspects them. -/
abbrev PayloadHash := String

/-- Which half of an exchange a booking records. -/
inductive Side where
  | send
  | receive
deriving DecidableEq

/-- An exchange booking: one side's record of an exchange, as kept
    in that side's own ledger. `tenant` is the booking entry's `ns`;
    `peer`, `side`, `ph`, `xchg` are the descriptor fields. -/
structure Booking where
  tenant : TenantId
  xchg : XchgId
  side : Side
  peer : TenantId
  ph : PayloadHash
deriving DecidableEq

/-- A ledger entry, abstracted to the fields the audit and the
    agreement rule read (see the module docstring). Amounts are
    integers — the integer skeleton of the conservation law (see
    the module docstring and README "Simplifications"). -/
inductive Entry where
  /-- `BIND` with `kind = "exchange"`: an exchange booking, plus
      the entry's recorded `g`. -/
  | booking (b : Booking) (g : Int)
  /-- `BIND` with `kind = "tenant"`, payload `{"budget": budget}`:
      a budget declaration, plus the recorded `g`. -/
  | declareBudget (ns : TenantId) (budget : Int) (g : Int)
  /-- `EFFECT`: recorded surprise `s` and recorded `g`. -/
  | effect (ns : TenantId) (s : Int) (g : Int)
  /-- `TICK`: resets the tenant's γ; recorded `g` is 0 by
      construction. -/
  | tick (ns : TenantId)
  /-- Any other stitch: γ unchanged, recorded `g` still audited. -/
  | other (ns : TenantId) (g : Int)

/-- A ledger: a sequence of entries, oldest first — the order of
    the lines in the JSONL file, and the order the audit replays. -/
abbrev Ledger := List Entry

/-- The tenant an entry belongs to (its `ns`). -/
def Entry.ns : Entry → TenantId
  | .booking b _ => b.tenant
  | .declareBudget ns _ _ => ns
  | .effect ns _ _ => ns
  | .tick ns => ns
  | .other ns _ => ns

/-- The entry's recorded γ (its `g` field). -/
def Entry.g : Entry → Int
  | .booking _ g => g
  | .declareBudget _ _ g => g
  | .effect _ _ g => g
  | .tick _ => 0
  | .other _ g => g

/-- The booking an entry records, if it is an exchange booking. -/
def Entry.booking? : Entry → Option Booking
  | .booking b _ => some b
  | _ => none

/-- An entry whose `booking?` is `some b` is a booking entry for
    `b`, carrying some recorded `g`. -/
theorem booking?_eq_some {e : Entry} {b : Booking} (h : e.booking? = some b) :
    ∃ g : Int, e = Entry.booking b g := by
  cases e with
  | booking b' g =>
      refine ⟨g, ?_⟩
      simp only [Entry.booking?] at h
      rw [Option.some.inj h]
  | declareBudget ns budget g => simp [Entry.booking?] at h
  | effect ns s g => simp [Entry.booking?] at h
  | tick ns => simp [Entry.booking?] at h
  | other ns g => simp [Entry.booking?] at h

/-- `l` contains a booking entry recording `b`. This is the
    membership form of `agreement.py:_bookings`: the entries it
    selects are exactly the `BIND`/`"exchange"` entries, and it
    returns their descriptors paired with the entries' `ns` — which
    `Booking.tenant` here carries. -/
def HasBooking (l : Ledger) (b : Booking) : Prop :=
  ∃ e ∈ l, e.booking? = some b

/-- The bookings for exchange `x` in `l`, as a list — `_bookings`,
    literally: scan the entries, keep the exchange bookings whose
    descriptor's `xchg` is `x`. -/
def bookings : Ledger → XchgId → List Booking
  | [], _ => []
  | e :: rest, x =>
      match e.booking? with
      | some b => if b.xchg = x then b :: bookings rest x else bookings rest x
      | none => bookings rest x

/-- The list form and the membership form of `_bookings` agree. -/
theorem mem_bookings {l : Ledger} {x : XchgId} {b : Booking} :
    b ∈ bookings l x ↔ HasBooking l b ∧ b.xchg = x := by
  induction l with
  | nil =>
      constructor
      · intro h; cases h
      · intro h
        obtain ⟨⟨e, he, _⟩, _⟩ := h
        cases he
  | cons e rest ih =>
      cases hb : e.booking? with
      | none =>
          have hstep : bookings (e :: rest) x = bookings rest x := by
            simp [bookings, hb]
          rw [hstep]
          constructor
          · intro h
            obtain ⟨h1, h2⟩ := ih.mp h
            obtain ⟨e', he', hb'⟩ := h1
            exact ⟨⟨e', List.mem_cons_of_mem e he', hb'⟩, h2⟩
          · intro h
            obtain ⟨⟨e', he', hb'⟩, hx⟩ := h
            cases he' with
            | head => rw [hb] at hb'; cases hb'
            | tail _ hmem => exact ih.mpr ⟨⟨e', hmem, hb'⟩, hx⟩
      | some b' =>
          by_cases hx' : b'.xchg = x
          · have hstep : bookings (e :: rest) x = b' :: bookings rest x := by
              simp [bookings, hb, hx']
            rw [hstep]
            constructor
            · intro h
              cases h with
              | head => exact ⟨⟨e, List.mem_cons_self, hb⟩, hx'⟩
              | tail _ hmem =>
                  obtain ⟨h1, h2⟩ := ih.mp hmem
                  obtain ⟨e'', he'', hb''⟩ := h1
                  exact ⟨⟨e'', List.mem_cons_of_mem e he'', hb''⟩, h2⟩
            · intro h
              obtain ⟨⟨e'', he'', hb''⟩, hx⟩ := h
              cases he'' with
              | head =>
                  rw [hb] at hb''
                  have hbb : b' = b := Option.some.inj hb''
                  subst hbb
                  exact List.mem_cons_self
              | tail _ hmem =>
                  exact List.mem_cons_of_mem b' (ih.mpr ⟨⟨e'', hmem, hb''⟩, hx⟩)
          · have hstep : bookings (e :: rest) x = bookings rest x := by
              simp [bookings, hb, hx']
            rw [hstep]
            constructor
            · intro h
              obtain ⟨h1, h2⟩ := ih.mp h
              obtain ⟨e', he', hb'⟩ := h1
              exact ⟨⟨e', List.mem_cons_of_mem e he', hb'⟩, h2⟩
            · intro h
              obtain ⟨⟨e', he', hb'⟩, hx⟩ := h
              cases he' with
              | head =>
                  rw [hb] at hb'
                  have hbb : b' = b := Option.some.inj hb'
                  subst hbb
                  exact absurd hx hx'
              | tail _ hmem => exact ih.mpr ⟨⟨e', hmem, hb'⟩, hx⟩

end Stitch
