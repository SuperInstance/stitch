/-
  StitchLean/Agree.lean — agreement and minting, with the
  soundness theorem.

  Models `agreement.py:agree` (bilateral, keyless semantics: v0.3's,
  which are also v0.4's whenever the auditor supplies no
  `booking_keys`). The opt-in signature enforcement of v0.4 is not
  modelled — see README "Simplifications".
-/

import StitchLean.Audit

namespace Stitch

section Agreement

variable {Hash : Type} (H : Hash → Entry → Hash) (seed : Hash)

/-- Both halves of the audit, as `agree` requires before it will
    look at any booking: the chain checks out and the law replay is
    clean. A broken chain or a law violation on either side refuses
    everything. -/
def Audited (C : Int) (stored : List Hash) (l : Ledger) : Prop :=
  ChainValid H seed stored l ∧ LawfulLedger C l

/-- The mirror rule (SPEC "Minting", step 4): `s` is a SEND booking
    in `A`; `r` is a RECEIVE booking in `B`; the receiver the send
    names (`s.peer`) is the tenant who booked the receive; the
    receive names the sender as its peer; and both commit to the
    same exchange id and the same payload hash — the mirror agrees
    exactly. -/
structure Mirror (A B : Ledger) (x : XchgId) (s r : Booking) : Prop where
  send_in : HasBooking A s
  recv_in : HasBooking B r
  send_xchg : s.xchg = x
  recv_xchg : r.xchg = x
  send_side : s.side = Side.send
  recv_side : r.side = Side.receive
  recv_tenant : r.tenant = s.peer
  recv_peer : r.peer = s.tenant
  same_ph : r.ph = s.ph

/-- Minting: both ledgers pass audit, and some SEND booked in
    either ledger is mirrored by a RECEIVE in the other. (Python
    scans ledger A's sends before ledger B's, making the *verdict
    deterministic*; which send witnesses the mint does not change
    whether it mints, so the model keeps the existential.) -/
structure Minted (C : Int) (storedA storedB : List Hash) (A B : Ledger)
    (x : XchgId) : Prop where
  audited_a : Audited H seed C storedA A
  audited_b : Audited H seed C storedB B
  mirror : ∃ s r : Booking, Mirror A B x s r ∨ Mirror B A x s r

/-- **Minting soundness.** If exchange `x` mints between ledgers
    `A` and `B`, then the two ledgers really do contain the
    witnessing pair of booking entries: one ledger holds an entry
    recording a SEND of `x`, the other holds an entry recording a
    RECEIVE of `x`, the receiver is the peer the send named, the
    receive names the sender back, and the two entries commit to
    the same payload hash. Nothing mints out of thin air: the
    Agreement record `agree` derives is always witnessed by entries
    in the two files themselves.

    (What this does not say — SPEC is equally plain — is that the
    payload ever moved, that a ledger copy is authentic, or that
    the tenants are who their identity strings claim. Agreement
    proves the two *records* agree.) -/
theorem minted_sound {C : Int} {storedA storedB : List Hash} {A B : Ledger}
    {x : XchgId} (h : Minted H seed C storedA storedB A B x) :
    ∃ s r : Booking, ∃ es er : Entry,
      ((es ∈ A ∧ er ∈ B) ∨ (es ∈ B ∧ er ∈ A)) ∧
      es.booking? = some s ∧ er.booking? = some r ∧
      s.xchg = x ∧ r.xchg = x ∧
      s.side = Side.send ∧ r.side = Side.receive ∧
      r.tenant = s.peer ∧ r.peer = s.tenant ∧ r.ph = s.ph := by
  obtain ⟨s, r, hm | hm⟩ := h.mirror
  · obtain ⟨es, hesA, hes⟩ := hm.send_in
    obtain ⟨er, herB, her⟩ := hm.recv_in
    exact ⟨s, r, es, er, Or.inl ⟨hesA, herB⟩, hes, her,
      hm.send_xchg, hm.recv_xchg, hm.send_side, hm.recv_side,
      hm.recv_tenant, hm.recv_peer, hm.same_ph⟩
  · obtain ⟨es, hesB, hes⟩ := hm.send_in
    obtain ⟨er, herA, her⟩ := hm.recv_in
    exact ⟨s, r, es, er, Or.inr ⟨hesB, herA⟩, hes, her,
      hm.send_xchg, hm.recv_xchg, hm.send_side, hm.recv_side,
      hm.recv_tenant, hm.recv_peer, hm.same_ph⟩

end Agreement

end Stitch
