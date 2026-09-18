# ADR-011: Build mode — a real target repo, a diff the panel judges, and a PR as artifact

- Status: **Proposed**
- Date: 2026-09-02
- Related: the 2026-09-02 framework audit (`decisions/plans/2026-09-02-framework-audit.md`
  §3 build-task trace, §6 Workflows/Routines rows, §8 Q4 — the operator
  decision this ADR records), BACKLOG 2026-07-28 "Target-repo build mode",
  ADR-009 (graph runtime: DAG is data, loop is code), ADR-010 (one arc, six
  fenced roles, the operator's shape pick), ADR-006 (the path-shaped push
  boundary residual this ADR must not widen).
- Nothing below is implemented. Every "is" in the Decision is a **requirement
  on the implementation**, not a claim about `src/` (Behavioral Rule 6).

## Context

The audit traced the ask *"add a `--verbose` flag to project X's CLI, run its
tests, open a PR"* through the graph runtime and found the runtime is
**text-in / text-out** end to end:

(Cites below name **symbols**, not line numbers: an ADR outlives the line
numbers it was written against, and four of the five original cites had
already rotted one release later.)

- `chimera new` has no repo or path argument (`cli.py` `cmd_new`); `AgentCall`
  carries no cwd (`models.py` `AgentCall`); `TaskSpec` carries no target at
  all — `target_repo` was a legacy retired-arc field read nowhere, **deleted
  in 7.2.0** and now listed in `queue.REMOVED_SPEC_FIELDS` so an unmigrated
  record refuses to load rather than silently dropping it. It returns as a
  **real, read field with this ADR**, and the change that re-adds it drops
  the entry from that tuple in the same commit.
- A maker node's Write/Edit lands wherever the driving session's Agent tool
  happens to be rooted — per `TICK_PROTOCOL.md`, the chimera checkout itself.
  The maker returns a `GraphNodeOutput.output` string, never a diff; the
  executor's Bash tests whatever is in that cwd and returns prose; the wrap
  merges both into `graph-output.md` (`arcs/graph.py` `_wrap_prompt` →
  `_submit_wrap` → `_render_artifact`).
- The terminal panel reads that markdown (`arcs/graph.py` `_verify_calls`,
  which renders the artifact and hands it to `verify/lite.py` `critic_calls`)
  through read+web fences with **no shell** (`roles.py` `FENCES`: `critic`
  holds `_READ_WEB`). It can confirm a cited path or URL exists. It cannot run
  `pytest`. A node claiming "803 passed" is verified by *reading the
  sentence*.
- Nothing produces a PR; nothing commits to a target repo.

So chimera can deliver verified research, analysis, scoping and prose today,
and cannot deliver *"code landed in repo X, tests green, PR open"* as a
**verified** outcome. Use-case fit scored 4/10 for exactly this reason.

Q4 of the audit's sign-off asked whether to extend the graph arc or prove the
seam first as a Claude Code Workflow. **The operator's decision of record:
prove it FIRST as a Workflow run from the TARGET repo's worktree, with
chimera supplying the ledger (task record, arc state, `verification.json`,
digest) and the two gates; the graph arc grows the seam only after one green
run.** This ADR specifies the seam that run is proving.

## Decision

### (a) A real target, threaded from G1 to the brief

`TaskSpec` gains **read** fields — `target_repo: str | None` (`owner/name`
form; the name is reused, the old legacy field is gone, so re-adding it also
means dropping `"target_repo"` from `queue.REMOVED_SPEC_FIELDS` in the same
change) and `target_ref: str | None` (default `main`) — set at G1 by `chimera
new --target-repo owner/name [--target-ref main]`. They thread into
`GraphArcState` (which pins them alongside `shape`, so a hand-edited state
file cannot re-point a run at a different repo mid-flight) and into both
prompt builders:

- `graph.worker_brief()` gains an explicit **"operate inside `<worktree
  path>`; every path you touch is relative to that root; do not write outside
  it"** instruction ahead of the node brief.
- `graph.checker_brief()` gains the same worktree line **without widening its
  signature's meaning**: a checker still sees the ask, its rubric, the read
  artifacts and its own id. The worktree path is *location*, not maker
  context — no maker reasoning, no sibling chatter, no plan rationale. The
  prompt-equality test extends to assert the path is the only addition.

The worktree itself is created by the driving session (a `claude/` branch off
`target_ref`), never by chimera.

### (b) The diff is the artifact, not the description

A build-shaped plan ends with an **executor node running `git diff` (and
`git diff --stat`) in the target worktree**, and the wrap carries the result
forward as a first-class field:

```
GraphArtifact.diff:      str | None   # unified diff, verbatim
GraphArtifact.diff_stat: str | None   # git diff --stat
GraphArtifact.pr_url:    str | None   # see (d)
```

The wrap maker's contract is unchanged — **it merges, it never authors** — and
that contract binds hardest here: a summarized, re-indented or line-wrapped
diff is an **authored** diff. The wrap copies the executor's output byte for
byte or sets the field `None`. The panel judges the diff; a `None` diff on a
build-shaped run is itself a refutation-worthy finding.

### (c) A seventh fenced role: `confirm` (read + shell, no write, no network)

Its brief is exactly one thing: **"re-run exactly the command the executor
claimed to run, in the target worktree, and report the output and exit code
verbatim; add nothing."** It is a checker role (`GRAPH_CHECKER_ROLES`), so
`graph.node_model()` derives it distinct from the producer it reads.

**Compound-grant proof** (`roles.check_grant`):

- **shell without write is constructible.** `confirm`'s grant is
  `("Read", "Grep", "Glob", "Bash")` — no member of `WRITE_TOOLS`, so neither
  the write+shell nor the write+network clause fires. `executor` already
  proves this shape validates.
- **confirm + maker in one grant is not constructible.** Their union holds
  `Bash` and `Write`, so `check_grant` raises `FenceViolation` at
  construction — not at a check site that can be forgotten. The role that
  re-runs the command therefore *cannot* be the role that could edit the code
  until it passed. That is the whole point: it closes the
  "claimed-test-run verified by reading the claim" hole without handing
  anyone write+shell.

**Stated residual, honestly:** `confirm`'s grant is byte-identical to
`executor`'s, so `roles.distinct_grants()` stays at four and the fence table
does **not** separate them by capability. The separation is semantic — the
brief, and checker-role membership for model derivation. A future audit
should read that as a known limit, not rediscover it. Adding the role means
`GraphRole`, `roles.FENCES`, `roles.ROSTER_NAME`, `agents.ROSTER` and
`GRAPH_CHECKER_ROLES` move in lockstep (`_assert_lockstep` makes a partial
addition fail at import), and `install-agents` writes seven files.

### (d) The PR is an artifact the panel resolves

`GraphArtifact.pr_url` is carried forward by the wrap like the diff. The
critic fence already holds `WebFetch` (`_READ_WEB`), so the GREP-ANCHOR
critic **fetches the URL, confirms it resolves, and confirms the PR's diff
matches the artifact's**. An unreachable URL, or one whose diff disagrees, is
`refuted=true` — the existing angle ("verify every cited source … any failed
substantiation → refuted=true") pointed at a PR instead of a citation. No new
angle; the panel stays at three.

This is **chimera's** panel (`verify/lite.py` `critic_calls`), which is the
only one that runs after the PR exists. In the prove-first shape the URL rides
in the wrap body rather than in `pr_url`, but the panel that resolves it is
the same one — see the acceptance criteria below.

### (e) chimera never commits to the target repo, and the record stays clean

The push boundary (Security Rule #1) is load-bearing and this mode moves
straight through it, so the boundary is specified before the feature:

1. **Who writes where.** The driving session's Agent tool commits in the
   target worktree, on a `claude/` branch, and opens the PR. chimera's own
   commits stay in `tasks/<id>/` in the chimera checkout. chimera never runs
   `git commit` or `git push` against the target, and arcs still never write
   into chimera's tree outside `tasks/`.
2. **Record-sanitization boundary (the load-bearing half).** `tasks/<id>/` is
   *committed and pushed to chimera's private origin* — so anything in arc
   state, the artifact, the digest, a run-summary or memory is one push from
   the origin, and the audit's P0-2 proves this failure mode is real, not
   theoretical. Therefore, per target:
   - **Non-sensitive target** (personal/public repos): `diff`, `diff_stat`
     and `pr_url` may ride in the artifact. Target *content* beyond the diff
     — file bodies, fetched data, logs — does not.
   - **Corp / third-party target**: `diff` is held `None` by construction; only
     `diff_stat` (paths + line counts) and `pr_url` are recorded. The panel
     still judges execution, because `confirm` re-runs the command **inside
     the worktree**, where the content already lives, and reports only its
     verdict upward. A verdict is not content.
   - Run summaries and the L2 memory layer take the stat and the verdict,
     never the diff.
3. **Corp/third-party targets run on-box, never on the cloud lane** — which holds no
   corp credentials and bypasses the untracked pre-push guard (ADR-006). A
   hard rule: the mode refuses rather than degrades. ADR-006's residual is
   unchanged and **not widened** — protection is still path-shaped, and
   *where* the diff is written stays part of the control.

## Alternatives considered

- **Managed Agents / Outcomes** (rubric + independent grader in a separate
  context, `max_iterations`, scheduled deployments). The shape is right — it
  is already what verify+repair does. **Rejected: it requires an API key**,
  against the framework's standing no-API-key posture (CLAUDE.md
  "deliberately absent"). The rubric-as-markdown pattern is worth stealing
  into the checker briefs; the runtime is not available to us.
- **Extend the `executor` fence with Write** so one role can edit and test.
  **Rejected: unconstructible.** `check_grant` refuses write+shell at
  construction, for exactly the reason that applies here — the role deciding
  whether the test passed could edit the code until it did. `confirm` exists
  because this alternative is closed.
- **Extend the graph arc first, prove later.** Rejected by the operator (Q4):
  with zero completions through G2, building the seam into the runtime before
  one green run hardens an unvalidated shape. Workflow first.
- **Build inside chimera's checkout and push the result over.** Rejected
  outright — the exact motion Security Rule #1 forbids, already ruled out by
  the BACKLOG entry ("This is NOT build in chimera then push over").

## Consequences

- Use-case fit becomes testable: "code landed, tests green, PR open" is a
  claim the panel can *refute with a shell and a fetch*, not a sentence it
  reads. The panel stays at three angles and the gates stay at two — build
  mode adds nodes and one role, no new gate and no new verification tier.
- `chimera new` grows two optional flags and `GraphArtifact` three optional
  fields, so every pre-ADR record still validates; `SCHEMA_REGISTRY` and the
  wrap prompt move with the schema.
- Verify payload pressure rises: a unified diff can be large against
  `VERIFY_PAYLOAD_MAX`. The loud-truncation notice covers it, but a build run
  is the first workload likely to hit the cap in normal use.
- The mode is only as clean as the target declaration. A corp target
  mislabelled as personal puts a diff on the private origin. That declaration
  is an operator input at G1 — the same class of control as the gitignored
  zones: discipline, made explicit.

## Acceptance test for leaving Proposed

ADR-011 moves from Proposed to Accepted on exactly one piece of evidence:

> **One real build-shaped task, green through G2**, run per
> `decisions/plans/2026-09-02-build-mode-prove-first.md` — a non-corp target
> repo, the Workflow driving the work from the target's worktree, chimera
> holding the ledger and both gates, and the verdict resting on (i) a diff
> read as a diff, and (ii) a PR URL fetched and matched against that diff.
> Refutations that fired count as evidence, not failure; a run where neither
> panel touched the diff or the PR does not count.

**Which panel proves which half — the two are not interchangeable.** In the
prove-first shape there are two REFUTE panels, and reading them as one is the
mistake this paragraph exists to prevent:

- **(i), the diff half, is proved by the Workflow's panel.** It runs in the
  target worktree, where the code and the test output actually are, and it can
  read the files the diff touches. It runs **before** the driving session
  commits, so at that moment **no PR exists** and the workflow's critics
  cannot possibly satisfy (ii). Their opinions are *evidence*, and they ride
  into the wrap body verbatim as such.
- **(ii), the PR half, is proved by chimera's own panel** — the three calls
  `verify/lite.py` `critic_calls` builds and `chimera tick` prints at the
  verify stage, over the wrap body that carries the PR URL. The GREP-ANCHOR
  angle there fetches the URL and matches it against the diff in that same
  body. This panel, not the workflow's, is what fills `verify:critic1..3`.

So a run that **replays** the workflow's three opinions into
`verify:critic1..3` fails this criterion by construction: those critics never
saw a PR URL, and no amount of re-reading their reasons makes them have
fetched one. The operator runs the chimera-printed critic calls at the verify
stage. Once the ADR is implemented, `GraphArtifact.pr_url` carries the URL as
a field and both halves collapse into that single panel; until then the wrap
body is where the URL lives and the split above is the honest description.

Until that run exists, this ADR is a specification and nothing in `src/`
should claim it.
