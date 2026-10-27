# 01 — The accountable journal

**The job:** your agent does real work on its own — here, processing
field notes — and afterwards a supervisor who never watched it run must
be able to answer: *what did it do, in what order, and did it stay
inside the limits it was given?* Not from the agent's say-so: from a
record the agent cannot quietly rewrite.

This is the template for any "agent with receipts" setup: support
agents closing tickets, research agents filing results, cron agents
doing nightly work.

## Run it

```bash
cd tutorials/01-accountable-journal
python3 work.py     # the agent does the job and journals it
python3 audit.py    # the supervisor checks the journal, cold
```

Real output (abridged; your head hash will differ — entries carry
timestamps):

```
  monday.txt: 1 action(s), effect committed=True, gamma now 1.00
  monday.txt: second write rejected (budget spent) — ticking and retrying
  tuesday.txt: 0 action(s), effect committed=True, gamma now 0.00
  wednesday.txt: 2 action(s), effect committed=True, gamma now 1.00
Journal: journal.jsonl — 17 entries, 3 action(s) total
```

```
chain_ok : True (broken at: None)
law_ok   : True (max excess: 0.0)
entries  : 17
budgets  : {'field-agent': 1.584962500721156}  (declared in the ledger itself)
attested : 'processed 3 action(s)' by field-agent
AUDIT: PASS
```

## What just happened

- **Every step of the job is a stitch in one append-only ledger.**
  `bind` a cell per note, `effect` to record what the note changed,
  `tick` to close the unit of work, `attest` to sign off at the end.
- **Surprise is the price of being wrong.** The agent predicts each
  note has no actions. A note that does costs 1 bit of its
  per-tick budget (γ). The quiet note costs 0. The journal therefore
  shows not just *what* the agent did but *where reality differed from
  its expectations* — usually the part a supervisor cares about.
- **Rejection is normal flow.** Monday's second write would have taken
  γ to 2.0 against a 1.585 budget, so `effect` returned `False`,
  appended nothing, and mutated nothing. The worker ticks and retries.
  Budget pressure shapes behaviour at write time, not in a post-hoc
  report.
- **The audit is independent.** `audit.py` replays the file: hash
  chain, conservation law, declared budgets. It never imports the
  worker's state. Try it: edit any number inside `journal.jsonl` and
  re-run — the chain breaks; append a hand-made overspend and the law
  replay catches it even with a valid chain (that's the difference
  between `verify()` and a real audit; see VERIFICATION.md).

## Adapt it

Marked in `work.py`: the job itself (`process_note` — swap in your
task: tickets, files, API results), the prediction (your agent's prior
about its work — this is what makes the surprise record meaningful),
and the tick unit (per note here; per task, per batch, per minute —
a tick is *your* quantum, there is no global clock). Set the budget in
both places — `Tenant(budget=…)` enforces, `declare_budget(…)` records —
to the same value.

## Limits, honestly

The journal proves the record is whole and the writes obeyed the law.
It does not prove the agent's *inputs* were honest: predicted/observed
values come from the writer, and a budget is self-declared (auditable,
not truthful). If the supervisor must also know the agent *itself*
wrote the entries, that's signatures — Tutorial 02's exercise adds a
signed attestation and a keyed audit.

## Exercises

1. Give the agent budget for two surprises per tick
   (`Tenant(budget=2.5)` + declaration) and watch the rejections
   disappear — then check what the audit reports about budgets.
2. Delete the last three lines of `journal.jsonl` and re-audit. The
   chain still verifies — what *would* catch this? (Tutorial 02's
   anchor.)
