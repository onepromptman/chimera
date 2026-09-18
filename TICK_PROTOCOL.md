# Tick protocol — the worker bootstrap

A chimera worker is a **Claude Code cloud session on the operator's subscription**
(no API keys, no Actions workers). Since 2026-09-02 the standing worker is a
**cloud Routine** (`chimera tick (dispatcher)`, every 4 hours, a fresh session
per fire) that follows this file; `/loop chimera tick` in an interactive
session is the manual alternative, never both at once. The chimera package is the
deterministic skeleton; the session is the runtime. This file is the prompt
a worker session follows. Paste it (or `/loop chimera tick` from a
dispatcher session) and the session becomes a worker.

## ONE worker at a time (hard constraint)

chimera runs **exactly one worker session at a time**. The claim is a local
check-then-write plus a push — there is no cross-clone reconciliation, so
two concurrent workers can both believe they hold a claim and the losing
session's pushes fail as non-fast-forward (audit F2). By doctrine, not code:

- Once a dispatcher loop is armed, **the dispatcher is THE worker**. Do not
  run manual or mobile `tick`s alongside it — kick the dispatcher instead.
- A lost race is visible, not silent: non-fast-forward pushes fail fast
  (no retry storm), and `chimera status` reports push health
  (`unpushed_commits` > 0 means your pushes are not landing — stop and
  reconcile manually).
- Check `chimera status` push health at session start and before signing
  off. Build pull-rebase reconciliation only if real overlap ever appears;
  until then this doctrine is the contract.

## Boundary rule (read first)

Nothing from the gitignored work zones (project content, former-employer-derived
material) ever enters this repo, its queue, or its sessions. If a task or
comment smells like work content, stop and point
the operator at local interactive Claude Code. Protection is the gitignored work
zones plus an optional, untracked, machine-local pre-push hook
(`~/.config/chimera/pre-push-guard.py`, where installed) — not part of the
framework — so work content never reaches the public remote.

## The loop

```
1. git pull origin <branch>            # queue state is git
2. python -m chimera tick              # flock-guarded; claims/resumes ONE task
   - "idle"  -> nothing runnable: stop (or re-arm if dispatching)
   - "work"  -> you hold the claim; proceed
   - "failed_tasks" in the output -> tick parked those jammed tasks as
     `failed` and moved on; mention them when you report, don't stop
3. **Execute the whole stage per turn, not one call per turn.** Take ALL
   pending_calls from the tick output at once: fan out every independent
   label in parallel (investigators, critics, judges — the native Workflow
   tool is the PREFERRED executor: deterministic parallelism +
   schema-validated returns), collect the results, then run the
   `arc submit` commands back-to-back in one pass. The schema gate validates
   every payload at submit regardless of batching — batching changes
   turn-granularity, never safety. (Protocol tightened 2026-07-25: the
   per-call courier loop predated frontier-model sessions and cost ~60
   round-trips per research task.)
   - Each call runs with YOUR native Agent tool:
       prompt        = pending_call.prompt
       model         = pending_call.model        # opus = maker, sonnet = critic; never swap
       subagent_type = pending_call.subagent_type # see "Explicit specialist selection"
       read-only subagents for critics/investigators
   - The agent must return ONLY the JSON object requested by the prompt.
   - Submit:  python -m chimera arc submit <task-id> <label> --file result.json
     (`--json '<object>'` works for one-line payloads; anything with quotes,
     newlines or `$` should go through `--file`)
   - Agent failed / refused / garbage after one retry?
              python -m chimera arc submit <task-id> <label> --null --kind threw
   - Schema-gate rejection (exit 1, "schema gate")? Re-run the agent once
     with the error appended; second failure -> submit --null.
4. Each submit prints the NEW pending calls. Keep going until either:
   - pending_calls is empty AND arc_phase == "complete": the CLI has already
     recorded verification, moved the task to `done` (verification passed —
     auto-approved) or parked it at awaiting-signoff (verification failed),
     and written the digest. Post `signoff_comment` from the submit output to the task's
     Issue (one Issue per task — create it from issue_title/issue_body if it
     doesn't exist). Before posting, scan the Issue thread: if the same
     comment kind is already there, don't repost.
   - arc_phase == "failed": the CLI has already moved the task to queue-state
     `failed` — it cannot block the loop. Post the failure to the Issue.
     the operator decides later: `chimera retry <id>` (fresh arc start) or
     `chimera archive <id>` (retire). If a task is wedged WITHOUT a terminal
     arc_phase (hung call, corrupt state), `chimera abandon <id> --note "..."`
     parks it as failed by hand.
5. Park-or-complete, then re-arm: run `python -m chimera tick` again for the
   next task; stop at "idle".
```

## Rules that are not optional

- **Never edit task state by hand.** Only the CLI mutates `tasks/` — every
  mutation is a commit (durable-state-first). If a container dies mid-arc,
  the next tick resumes from the last commit; in-flight agent calls are the
  only loss.
- **You cannot declare done.** `done` happens only inside the CLI: at arc
  completion the submit path auto-approves a PASSING verification and
  transitions to `done` itself (G2 is review-after — the operator reads the digest
  and `chimera reject <id>` reopens); a FAILING verification parks at
  awaiting-signoff, where `chimera approve`/`reject` (G2, the operator) apply
  behind the verify gate inside `queue.transition()`. Since 2026-09-02
  the G2 verbs (`approve`/`reject`) also require the **operator token**: its
  SHA-256 digest is committed at `.g2-operator.sha256`, and G2 reads that
  COMMITTED blob — editing or deleting the file in your working tree changes
  nothing, because you own that tree. The token itself
  lives only on the operator's machine (`CHIMERA_OPERATOR_TOKEN` or
  `~/.config/chimera/operator-token`, created by `chimera g2-token init`). A
  worker session never holds it. Your job ends at the terminal state the
  submit output reports; you never run `approve`.
- **Models are pinned.** Maker calls = `opus` alias (newest Opus-class model);
  critic calls = `sonnet` alias ×2 (one on the critic tier, one executing on
  the research tier). Maker ≠ checker is asserted in code; don't route around
  it. `verify:mutant1` is a control — dispatch it verbatim like any verify
  call, never tell the agent it is a mutant.
- **Throughput > latency.** The 250-call ceiling fires fleet-wide. Timeouts do
  too: `chimera tick` / `chimera arc next` persist each pending call's
  first-issue stamp as they emit it, so a resumed session doesn't reset its
  own clock, and an aged-out call is routed as a **recoverable null** at the
  arc layer — a timeout is a learnable degrade, never a new halt class. The
  shared default ceiling is 300s per call (the verify critics run on it); the
  graph arc's `plan`, `node:` and `wrap` calls get 1800s
  (`arcs/graph.py` `CALL_CEILINGS`). `chimera status` surfaces the oldest pending call's
  label + age for each running task, so a stall is visible without opening
  `arc-state.json` by hand. If you get rate-limited, slow down — checkpoint
  resume means throttling costs time, never work.
- **Commit messages are the audit trail.** The CLI writes them; don't
  squash, amend, or force-push.

## Explicit specialist selection

Arcs select a specialist per call and emit it as `subagent_type` — this is how
the graph arc names one of the six fenced roles per node
(planner/researcher/maker/executor/critic/judge). Honor it:

- **`pending_call.subagent_type` is set** (one of the six roles — e.g. `maker`
  for a write node, `critic` for a REFUTE node): invoke `Agent(subagent_type=<that>,
  model=pending_call.model)` **EXPLICITLY**. Never rely on auto-delegation by
  free-text description — it is documented as unreliable; named selection is the
  only reliable invocation. Since 2026-09-03 this is load-bearing, not just
  tidy: the role prelude and the output-contract SHAPE live in the installed
  role file (`chimera install-agents`), and `pending_call.prompt` carries only
  the call-specific content plus the schema NAME. Dispatch without the
  `subagent_type` and the agent is missing half its instructions.
- **`pending_call.subagent_type` is null on a `build`/`test` call**: this is a
  general-purpose pick, legitimate ONLY because `selection_reason` /
  `selection_confidence` justify it (no external specialist's domain fit the
  work). The verify panel reviews unjustified generalist picks; do not silently
  swap in a specialist or a generalist of your own choosing.
- **Session-provisioning contract.** chimera *names + validates* specialists from
  its registry; it does not ship their definitions (the repo has no
  `.claude/agents/`). The driving session MUST provide the named subagents
  (account/global `~/.claude/agents/`). If `subagent_type` does not resolve in
  your session, run `python -m chimera install-agents` FIRST (it writes the
  six role files into `~/.claude/agents/`); only if install is impossible surface
  the gap — do not silently fall back to general-purpose. (An un-run install
  was a silent contract violation on every arc run before 2026-07-25.)

## Reflect ("dream") shape — RETIRED as a command (v7, ADR-010)

There is no `chimera reflect` subcommand. The reflect arc was deleted in
7.0.0 along with the other seven fixed pipelines; `reflect` now appears only
in `models.RETIRED_ARCS`. The shape survives as something the planner can
compose on the one live arc: a self-improvement run that reads SOURCE signal
only — the L2 run summaries in the memory DB and the committed
`tasks/<id>/verification.json` outcomes — and emits a proposal. (Router
telemetry is NOT a source: the hook writes a bare counter to a gitignored
path, so there is nothing committed to read.) If you run one, it rides the SAME verify gate + G2 as any graph run
and can NEVER self-apply — applying a reflect proposal is a human action after
`chimera approve`.
Drive it with the normal `arc submit` loop, then post `signoff_comment`.

## Wake-ups

- Dispatcher: the cloud Routine (every 4 hours) is the ONE worker while it is
  enabled; pause it at claude.ai/code/routines before running `/loop chimera
  tick` or a manual tick from another session — route kicks through one
  worker only.
- Issue activity on a task wakes a subscribed session: `/approve` comment ->
  `chimera approve <id>` then `chimera archive <id>` and close the Issue.
- Manual kick from web/mobile: just say "tick chimera" (only when no
  dispatcher is armed).

## One-time setup in a fresh container

```bash
pip install -e ".[dev]"     # pinned: pydantic 2.13.4, pytest 9.0.3
python -m pytest -q          # green before you touch the queue
python -m chimera status     # see the board + push health
```
