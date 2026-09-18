---
name: chimera-check
description: Use when the user picks "Do it + check", or asks to "check this", "gate 0 this", "anchor-check this", or "make sure this actually runs". Do the work, then run the two cheap falsifiers from the chimera ladder — an executor that actually runs the result (Gate 0) and, for claims, plans and docs, a critic that verifies every cited path and URL (Gate 1). Not a panel: for a hard-to-undo change use chimera-graph instead.
---

# Chimera check — Gates 0 and 1, in any repo

The cheap half of the chimera ladder, as its own skill. It runs **here, in
whatever repo the session is already in**. No queue, no ledger, no Python.

The cheapest check is also the one most likely to fail, so it goes first and it
goes on ordinary work. Three critics on a change whose tests already fail is
waste; one executor that runs it is not.

## Roles

Dispatch by name with the Agent tool. Both come from the `chimera` plugin (or
`chimera install-agents`):

| Role | Grant | Used for |
|---|---|---|
| `executor` | read + **shell**, no write | Gate 0 — run it, report verbatim |
| `critic` | read + network, no write | Gate 1 — anchor every citation |

If either name does not resolve, or resolves to an agent with a different grant,
**stop and say so**. An unfenced checker reports the same words with none of the
guarantees.

## Procedure

### Step 1 — do the work

You are the maker. Finish the change or the artifact first. Note the commands
that prove it works (tests, build, lint, the script itself) and what exit code
each should return.

### Step 2 — Gate 0, EXECUTE (always)

Dispatch `executor` with only `{the ask, the commands, the expected exit
codes}` — never your reasoning about why it should pass. Tell it:

> Run each command. **State the expected exit code before running it.** Report
> the verbatim output and the real exit code. Do not fix, interpret or
> summarize. Mark any command whose exit code missed its expectation.

Then read the result yourself:

- **Check the count, not only the exit code.** "0 tests collected" also exits 0.
- **Nothing to run is not an exemption.** Construct the check: grep for the
  strings the change claims to add, parse the config it says is valid, run the
  script with `--help`, re-count any number the text calls measured.
- Red → repair once → re-run Gate 0. Red twice → **stop and report**; do not
  grind.

### Step 3 — Gate 1, ANCHOR (claims, plans, docs, anything citing sources)

Skip only when the result is pure code with no prose claims. Dispatch `critic`
with only `{the ask, the artifact}` — **never tell it how Gate 0 went**; a
reader told "the tests passed" judges differently, and worse. Tell it:

> Verify every cited source. For each file path, read or grep it and confirm
> the claim is there. For each URL, fetch it and confirm. Any failed
> substantiation, or anything you could not verify, means `refuted: true`.
> Return exactly `{"refuted": bool, "reason": "...", "severity":
> "fatal" | "material" | "cosmetic"}`.

A reply in any other shape is invalid — treat it as a failed check, do not
coerce it.

**Gates 0 and 1 are checks, not votes.** Either one failing stops you: repair
once, re-run that gate, and report if it fails again.

## Escalate instead of stretching

If the work turns out to be hard to undo — a schema, auth or trust boundary, a
migration, money, a public release, a dependency that cannot be swapped later —
say so and offer `chimera-graph`, which adds the three-checker panel. Do not
turn this skill into a panel by adding critics.

## Models

Checkers must not run on the maker's model. `executor` and `critic` default to
`sonnet`; if this session is itself running on that model, say so in the report,
because the check is then weaker than it looks.

## Report

Keep it short and plain:

- **Gate 0:** each command, expected vs real exit code, and the count that
  matters (tests collected and passed, files changed, rows produced)
- **Gate 1:** checked / refuted, with the reason verbatim, or "skipped — pure
  code, no citations"
- **Repair:** what changed in the one lap, or "none"
