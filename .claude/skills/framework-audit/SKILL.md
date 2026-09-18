---
name: framework-audit
description: >
  Run a full adversarial self-audit of the chimera framework: is it
  daily-driver grade, where is it overbuilt/underbuilt, is the security
  boundary actually holding, is the memory/self-improvement loop actually
  compounding, and what should be stolen from the current agent-framework
  landscape? Fans out six evidence lanes (core engine, arcs+verify, docs
  fidelity, sandboxed UX walkthrough, security+boundaries, memory+compounding)
  plus a live web landscape sweep, then synthesizes a scored report into
  decisions/plans/ with a machine-readable finding trail in decisions/audits/,
  and feeds accepted items to BACKLOG.md or G1 intake. Use quarterly, after a
  major version lands, or before investing in a new subsystem. Trigger
  phrases: "framework audit", "audit chimera", "is this daily-driver grade",
  "what should chimera steal", "landscape scan".
---

# Framework audit (chimera auditing chimera)

Applies chimera's own method to itself: maker ≠ checker (audit lanes are
independent subagents; the synthesizing session judges; the operator gates
application), adversarial evidence standards, externalized durable output,
memory read before AND written after. An audit that produces vibes instead
of file:line evidence has failed. An audit that repeats last quarter's
findings without noticing has also failed.

## Cadence

Quarterly, or on any of: a minor/major version landing, a new arc shipping,
a "should I build X?" decision, or the harness/platform shipping a feature
that overlaps chimera (workflows, scheduling, memory, teams).

## Load-bearing rules

- **Evidence or it didn't happen.** Every finding carries `file:line`, a
  command + output, or a URL. Run the code — never trust docs or memory;
  the docs are themselves an audit *target*.
- **Evidence may only quote TRACKED framework files.** The report is
  committed and pushed to the public remote (Security Rule #1). Lanes will
  inevitably *observe* runtime state — `memory.db` rows, digest output,
  anything under gitignored zones (`overlay/`, `projects/`, `wip/`,
  `scratch/`, `data/`, `flows/*/output/`) — but none
  of that content may be quoted, paraphrased, or pathed into any output
  file. Describe such evidence structurally ("memory.db holds N rows; 3
  duplicate keys observed"), never by content.
- **Leak-scan before push.** After writing the report(s) and before the
  commit is pushed, scan every output file for work/personal tokens (the
  mirror-audit skill's scan list is the reference). mirror-audit's scan is
  advisory; here it is a **hard block** — a hit means rewrite the finding
  structurally, then rescan.
- **Maker ≠ checker applies to the audit itself.** Evidence lanes run as
  subagents on the critic tier (sonnet-class); the synthesizing main
  session (opus-class) judges, scores, and writes. A lane never writes to
  the repo; the synthesis session never generates lane evidence from its
  own memory of the codebase — it only weighs what lanes returned.
- **The repo under audit is read-only for lanes.** Only the synthesis
  session writes, and only the report + skill/BACKLOG updates.
- **UX lane runs in a sandbox clone** (`git clone` into the scratchpad, own
  venv, own git identity). The CLI commits on every transition by design —
  those commits must land in the clone, never the real repo.
- **Router interplay:** the repo's `router-enforcer` hook fires on Agent
  calls in-session but never blocks and never routes — since 2026-09-02 it
  is a counter that records only which subagent type was asked for (the
  decision path and its `[force-general-purpose]` bypass token are deleted).
  Spawn lanes freely; the hook is not an obstacle and is no longer an
  evidence source.
- **Balance is mandatory.** Each lane returns STRENGTHS before findings.
  An audit that can't name what's good can't be trusted about what's bad.
- **Findings are proposals.** Nothing is applied during the audit. Accepted
  items go to `BACKLOG.md` (dated entry) or straight to
  `python -m chimera new "..."`; big ones get an ADR. G2 spirit: the operator
  decides what lands.

## Phase 0 — priors (before any lane spawns)

1. `python scripts/chimera_memory.py search "framework audit"` (plus
   per-lane topic searches) — search-before-draft is law and an audit is
   a draft.
2. Read the most recent `decisions/plans/*-framework-audit.md` and
   `decisions/audits/*-framework-audit.jsonl` if they exist. Extract every
   prior finding and its disposition (accepted → BACKLOG/G1 / rejected /
   unaddressed).
3. Build the **delta table**: prior findings now FIXED / REGRESSED /
   STILL-OPEN. Hand each lane its slice of prior findings so repeat
   offenders get flagged as such — a still-open P1 from last quarter
   outranks a new P2.

First-ever run: note "no priors" and proceed.

## The seven lanes (fan out 1–6 in parallel; 7 runs in the main session)

1. **Core engine** — `models/queue/runner/gitio/gates/digest/notify/
   routing/agents/voices/brands/cli` + hooks. Verify every load-bearing
   CLAUDE.md claim (done-only-via-transition, commit-per-transition,
   ceilings, maker≠checker refusal, intake-is-in-session) as
   TRUE/PARTIAL/FALSE. Hunt races, swallowed errors, dead/sleeping surface
   (quantify live vs sleeping lines).
2. **Arcs + verify + prompts** — per-arc invariant parity table
   (maker≠checker, schema gate, null-degrade, verify stage, resumability,
   checkpoint-per-step), cross-arc duplication/drift cost of the
   standalone-by-design choice, verify-gate edge cases, prompt quality
   (are critics genuinely REFUTE-framed?), test fidelity (behavioral vs
   shape-only).
3. **Docs fidelity** — every checkable claim in CLAUDE.md / README /
   TICK_PROTOCOL / BACKLOG / CHANGELOG verified against code; claim table
   with ACCURATE/STALE/WRONG; contradictions between docs; undocumented
   real surface. Exact CLI strings operators will paste get priority.
4. **Daily-driver UX** — in the sandbox clone: fresh install → pytest →
   full lifecycle (`new → tick → arc submit … → verify → approve →
   archive → digest`), one deliberate schema-gate rejection, one `--null`
   degrade, one resume-after-kill drill. Metrics: commands per task,
   submits per arc, blocking gates, error-message quality, minutes to
   first value.
5. **Security + boundaries** — each of the 9 Security Rules verified
   TRUE/PARTIAL/FALSE with evidence; gitignore fence audit
   (`git check-ignore` every zone the File Organization table promises is
   ignored); secret scan of tracked files; hook posture (the fail-open
   wrappers: what actually happens when they crash?); Pydantic
   trust-boundary coverage (`extra="forbid"` everywhere claimed); a
   push-boundary dry run — on a clean clone with no local pre-push guard,
   what WOULD a leak look like and would anything catch it?
6. **Memory + compounding** — the self-improvement loop audited end to
   end: is memory actually read before drafts (grep call sites, not
   docs)? Written after work? Deduped? Is L2 `arc_memory` seeding wired
   or sleeping? (L3 `role_memory` was removed 2026-09-02 — if a doc still
   describes it as live or sleeping, that is a finding.) Does
   reflect consume real evidence (do the `decisions/audits/` globs
   resolve to files that exist?) and have its past proposals been acted
   on, or do they rot? Quantify: memory reads / writes / reflect
   proposals accepted within the audit window.
7. **Landscape sweep** (main session, live web) — what shipped in: the
   Claude platform (Code changelog, Managed Agents, memory features),
   always-on assistants (OpenClaw, Hermes, successors), memory tooling.
   For each relevant item: what it does better, what chimera does better,
   steal-or-skip with effort. **Scope cap: ≤15 fetched sources.**
   Press-derived claims are marked `[press]`; anything `[press]` must be
   re-verified against primary docs before an accepted steal-list item is
   built. Update or supersede `decisions/plans/*-agent-landscape.md`.

## Severity ladder

- **P0** — security/boundary hole or data-loss path; fix before next use.
- **P1** — a load-bearing documented invariant is FALSE, or a daily-loop
  blocker; fix this quarter.
- **P2** — drift, dead surface, missing test, UX friction; batch into
  normal work.
- **P3** — polish; record and move on.

A STILL-OPEN finding from a prior audit escalates one level.

## Scoring rubric (synthesis)

Score 0–10 with one-line justification each: **consistency** (code + docs
agree with themselves), **fidelity** (docs/tests tell the truth about the
code), **quality** (correctness, error handling, test strength),
**boundary safety** (would Rule #1 hold on a bad day — leak paths, guard
coverage, hook failure modes), **use-case fit** (does the daily loop serve
real work), **autonomy** (how much runs without a human hand-crank),
**self-improvement** (does the system compound: memory read AND written,
reflect fed AND acted on). Verdict: daily-driver grade? — plus the single
highest-leverage change. Include per-axis deltas vs the previous audit's
scores (first run: baseline).

## Output contract

- Report: `decisions/plans/YYYY-MM-DD-framework-audit.md` — scores (with
  prior-audit delta), per-lane findings ranked P0–P3 with evidence,
  do-MORE / do-LESS lists, a now/next/later roadmap.
- Finding trail: `decisions/audits/YYYY-MM-DD-framework-audit.jsonl` —
  one line per finding (`{id, lane, severity, claim, evidence_ref,
  disposition}`); the machine-readable input for the next audit's Phase 0
  and a future reflect evidence source. **Never** use the
  `router-audit-*` prefix — it is retired (the router-telemetry rotation was
  deleted 2026-09-02, audit Q12) and the existing files under it are frozen
  history, not a live series.
- Landscape: `decisions/plans/YYYY-MM-DD-agent-landscape.md` (or update
  the standing one) with a source list.
- Memory: after the report commits, write one memory entry per P0/P1 and
  per accepted item (dedup-on-write applies) so the next audit — and any
  arc touching the same module — retrieves them.
- Accepted actions → BACKLOG.md entries or `chimera new` intakes; doc
  fixes found by lane 3 may be applied in the same session **after** the
  report is committed (they are framework adjustments, Rule #1-safe).
- Order of operations is fixed: leak-scan outputs → commit report(s) →
  push with backoff → memory writes → BACKLOG/G1 routing → doc fixes.
