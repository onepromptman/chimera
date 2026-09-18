---
name: chimera-dag
description: Use when the user picks "Chimera DAG", or asks to "queue this in chimera", "run this through the DAG", or "tick chimera". Runs the full chimera engine from this checkout — G1 intake with `chimera new`, then the tick loop (planner DAG, fenced nodes, 2-critic verify) until the task is done, parked, or failed. Personal, non-work asks only. Works only inside the chimera repo.
---

# Chimera DAG — the engine, driven from this session

The heavy lane. A run takes the better part of an hour and produces a verified
brief with a committed record. Reach for it when the ledger itself is wanted;
for a quick check use `chimera-check`, for a verified decision in another repo
use `chimera-graph`.

`TICK_PROTOCOL.md` is the canonical worker procedure. This skill is the on-ramp
to it — read it before the first tick of a session and follow it where the two
differ.

## Preconditions — check all, stop on any miss

1. **You are in the chimera checkout.** `git rev-parse --show-toplevel` contains
   `src/chimera/`. Anywhere else, stop: work aimed at another repo runs in that
   repo (CLAUDE.md Behavioral Rule 7).
2. **The ask is personal, not work content** (CLAUDE.md Security Rule 1). If it
   names an employer, a colleague, a candidate, internal data or a work repo,
   refuse and offer `chimera-graph` in that repo instead.
3. **The engine is installed.** `.venv/bin/python -m chimera --help` runs. If not,
   follow README → Setup.
4. **The six roles resolve.** `executor`, `critic`, `maker`, `planner`,
   `researcher`, `judge` are available to the Agent tool. If not:
   `PYTHONPATH=src .venv/bin/python -m chimera install-agents`.
5. **One worker at a time.** `.venv/bin/python -m chimera status` shows no task
   claimed by another session, and no dispatcher Routine is armed. Never run
   alongside one.
6. **Pushes.** Every queue transition is a commit, and the CLI pushes to
   `origin`. Tell the user once, before the first tick, which remote and branch
   those commits will land on.

## Intake (G1)

```bash
.venv/bin/python -m chimera new "<the ask>" --shape diamond
```

- `diamond` (one strong maker, independent checkers) is the default for
  decisions. `straight` for one bounded deliverable, `pipeline` for staged work.
  Omit `--shape` to let the planner propose within the levers.
- If the ask is genuinely ambiguous, clarify it **in-session, before
  running `chimera new`**. There is no async question-and-park branch to
  fall back on.

## The loop

Per `TICK_PROTOCOL.md`:

1. `.venv/bin/python -m chimera tick` — claims or resumes one task and prints its
   `pending_calls`. `idle` means nothing is runnable: stop.
2. Take **all** pending calls at once. Dispatch every independent one in
   parallel with the Agent tool, using exactly `subagent_type`, `model` and
   `prompt` from each call. Never swap a model; never fall back to
   general-purpose when a role is named.
3. Save each JSON result to a file under `wip/` (gitignored) and submit:
   `.venv/bin/python -m chimera arc submit <task-id> <label> --file <path>`.
   Failed or garbage after one retry: `--null --kind threw`. Schema-gate
   rejection: re-run once with the error appended, then `--null`.
4. Each submit prints the next pending calls. Repeat until `arc_phase` is
   `complete` or `failed`.

Never edit `tasks/` by hand, never amend or force-push the CLI's commits, and
never run `chimera approve` — a worker cannot declare done.

## Report

- task id, final queue state, verification tally
- where the artifact is: `tasks/<id>/artifacts/`
- if it parked at `awaiting-signoff`: `chimera approve` / `reject` are the
  operator's and need the G2 token on this machine
- if it failed: the failure line, and `chimera retry <id>` or `chimera archive <id>`
