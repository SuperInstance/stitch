#!/usr/bin/env python3
"""
Tutorial 01 — the worker: an agent that does a real job and keeps an
accountable journal of it.

THE JOB (adapt this part): read every note in notes/, count its ACTION
lines, and produce a summary. Everything the agent records about the
job goes through stitch, so a supervisor can audit it later with
audit.py — without trusting this process, or this machine.

Run:  python3 work.py
Out:  journal.jsonl in this directory (deleted and rewritten each run).
"""
import sys
from pathlib import Path

# --- Getting stitch importable (the pattern every tutorial uses) -------
# This repo does not need pip: point sys.path at the in-repo package.
# In your own project use whichever route fits — pip install, a vendored
# copy, or this same path insert with your own layout. What matters is
# that `import stitch` below is the ONLY coupling to the repo's layout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stitch-python"))

import stitch

HERE = Path(__file__).parent
NOTES = HERE / "notes"
JOURNAL = HERE / "journal.jsonl"


def process_note(agent, ledger, surprise, note_path: Path) -> int:
    """Record one note in the journal. Returns its ACTION count."""
    text = note_path.read_text(encoding="utf-8")
    actions = [ln for ln in text.splitlines() if ln.startswith("ACTION:")]

    cell = stitch.bind(agent, ledger, kind="note",
                       payload={"file": note_path.name})

    # The agent's honest prior: "this note has no action items."
    # If that prediction is wrong, the write costs surprise — 1 bit for
    # the one key that differs. A quiet note costs 0. That cost is the
    # accountability: an agent that is surprised by everything runs out
    # of budget fast, and the journal shows where it spent its surprise.
    ok = stitch.effect(agent, ledger, surprise, cell, "actions",
                       predicted={"actions": "0 found"},
                       observed={"actions": f"{len(actions)} found"})
    print(f"  {note_path.name}: {len(actions)} action(s), "
          f"effect committed={ok}, gamma now {agent.gamma:.2f}")

    # A second write in the SAME tick: also record a one-word verdict.
    # If the note already spent this tick's budget on surprise, this
    # write is REJECTED — nothing is appended and the cell is unchanged.
    # Rejection is normal flow, not an exception: tick and retry.
    ok = stitch.effect(agent, ledger, surprise, cell, "verdict",
                       predicted={"verdict": "routine"},
                       observed={"verdict": "needs-follow-up" if actions else "routine"})
    if not ok:
        print(f"  {note_path.name}: second write rejected (budget spent) "
              f"— ticking and retrying")
        stitch.tick(agent, ledger)
        ok = stitch.effect(agent, ledger, surprise, cell, "verdict",
                           predicted={"verdict": "routine"},
                           observed={"verdict": "needs-follow-up" if actions else "routine"})
        print(f"  {note_path.name}: retry committed={ok}")
    stitch.tick(agent, ledger)  # one note per tick: a tick is YOUR unit
    return len(actions)


def main() -> None:
    if JOURNAL.exists():
        JOURNAL.unlink()

    # THE TWO BUDGET KNOBS — set both, to the same number.
    #   Tenant(budget=C)     is what `effect` ENFORCES at write time.
    #   declare_budget(C)    records that choice IN the ledger, so an
    #                        auditor doesn't have to take your word.
    # Declaring alone does not raise your write-time budget.
    agent = stitch.Tenant(identity="field-agent", budget=stitch.LOG2_3)
    ledger = stitch.Ledger(path=str(JOURNAL))
    stitch.declare_budget(agent, ledger)  # declares tenant.budget (LOG2_3)

    total = 0
    for note in sorted(NOTES.glob("*.txt")):
        total += process_note(agent, ledger, stitch.KLSurprise(), note)

    # Governance stitch: the agent attests to the finished job. (This is
    # the entry Tutorial 02's signing exercise teaches you to sign.)
    last = stitch.bind(agent, ledger, kind="job",
                       payload={"job": "process-notes"})
    stitch.attest(agent, ledger, last, claim=f"processed {total} action(s)")
    ledger.close()  # appends flush per entry; close when done anyway

    n = sum(1 for _ in open(JOURNAL, encoding="utf-8"))
    print(f"\nJournal: {JOURNAL.name} — {n} entries, {total} action(s) total")
    print("Now audit it like a supervisor would:  python3 audit.py")


if __name__ == "__main__":
    main()
