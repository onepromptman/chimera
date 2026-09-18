# Arc Chain Specification

> **v7 (2026-08-28):** The live arc surface is **ONE: `graph`**
> (every new task is a graph task; the planner picks the shape). The eight
> fixed pipelines named below are RETIRED (`models.RETIRED_ARCS`): their
> records still load, they never dispatch. Read the per-arc contract sections
> below as the *shapes the planner composes as data*, not as live execution
> surfaces. Amendment 2 of the v6 header is superseded by this note;
> amendments 1 and 3 still hold.
> See ADR-009 and ADR-010.

> **v6 (2026-06-10):** This SPEC remains law for arc shape and artifact
> contracts, with three amendments:
> 1. **Schemas live in code.** Every frontmatter/payload contract below is
>    validated by the Pydantic models in `src/chimera/models.py`
>    (`ResearchFrontmatter`, `DesignFrontmatter`, …) via
>    `src/chimera/verify/schema_gate.py`. The YAML blocks below are
>    documentation; `models.py` is the enforcement surface.
> 2. **[SUPERSEDED IN v7 — see the v7 note above.]** Live surface was all
>    eight arcs: research, design, proposal, build,
>    n8n, comms, reflect (off-critical-path), and gemini — all realized in
>    `src/chimera/arcs/`. The Arc 1–3 contract sections below (research,
>    proposal, build) remain law for their implementations.
> 3. **Artifacts live with the task.** v6 outputs land at
>    `tasks/<task-id>/artifacts/` (committed per checkpoint), not
>    `flows/<arc>/output/<slug>/`. Slug stability and frontmatter rules are
>    unchanged.

The chimera pipeline chains arcs. Each arc invokes a fixed sequence of
steps, passing a typed artifact forward at each step.

## Chain shape

```
research ──findings.md──> proposal ──proposal.md──> build ──manifest.md──> comms ──digest.md
```

A slug propagates unchanged through all four arcs. The same `<slug>`
identifies the work across every artifact produced.

---

## Arc 1: research

**Spine (v5, historical):** `/kickoff → /architect (scope) → /swarm-research → /5gate (validate findings) → /wrap-up`
**Execution (v6):** `src/chimera/arcs/research.py` — workflow.js v2.1 port as a resumable state machine (3-plan tournament + 3-critic REFUTE + 2-judge panel + tiebreaker + synthesis with grafts + goal-review loop). See `TICK_PROTOCOL.md` for invocation; state persisted to `tasks/<id>/arc-state.json`.
**Output artifact:** `tasks/<task-id>/artifacts/findings.md`

### Step contract

| Step | Skill | Reads | Writes |
|---|---|---|---|
| 1 | `/kickoff` | user question + optional context | scoped task spec |
| 2 | `/architect` | task spec | research scope + sub-question split |
| 3 | `/swarm-research` | sub-questions | N parallel agent results |
| 4 | `/5gate` | aggregated results | validation findings + revisions |
| 5 | `/wrap-up` | validated findings | `findings.md` + memory captures |

### findings.md frontmatter

```yaml
arc: research
slug: <kebab-case-slug>
created: <ISO8601>
sources: [<url|file>, ...]
sub_questions: [<q>, ...]
status: complete
```

---

## Arc 1b: design (v6)

**Execution:** `src/chimera/arcs/design.py` v0.1 — single-deliverable
iteration runbook ported from the prior-repo design team. Its prompt
templates (copywriter, deck-designer-v2, design-critic,
data-viz-specialist, consistency-critic, infographic-designer,
marketing-asset-designer) and the `models.BrandContext` token carrier they
injected are gone with the arc — the templates with ADR-010, `BrandContext`
2026-09-02. Recover them from git history; the surviving distilled craft is
in `decisions/references/`.

**Shape (v0.1):** brief (Opus scoper picks builder role + dimensions +
audience from the ask) → iterate loop per deliverable: builder (Opus,
chosen role) → 3 design-critic agents in parallel (Sonnet, never
aligned, scored on the 10-axis rubric) → `compute_aggregate` (deterministic:
proportional-majority survival, `min >= 8` convergence, plateau
detection drift `<0.3` at iter ≥3, max 5 iterations) → wrap (Opus
synthesizer assembles `DesignArtifact`) → lite verify → awaiting-signoff.

**Shape (v0.2, on first real multi-deliverable need):** multiple
deliverables iterate in parallel; after all converge, set-level
`consistency-critic` (Sonnet) scores 5 axes of cross-deliverable
coherence. Schemas (`ConsistencyVerdict`, `DesignArtifact` list-style)
are already in `models.py`; the state machine in `arcs/design.py`
extends per the runbook.

**Output artifact:** `tasks/<task-id>/artifacts/` assets + frontmatter
per `models.DesignFrontmatter` (arc, slug, created, brief, assets,
deliverable_role, iterations, final_min_axis_score, status).

## Arc 2: proposal

**Spine (v5, historical):** `/kickoff → /architect → exec-advisor → /5gate (review) → /wrap-up`
**Execution (v6):** `src/chimera/arcs/proposal.py`
**Output artifact:** `tasks/<task-id>/artifacts/proposal.md`

### Step contract

| Step | Skill / agent | Reads | Writes |
|---|---|---|---|
| 1 | `/kickoff` | research findings.md + user direction | scoped proposal spec |
| 2 | `/architect` | scoped spec | 3+ options + trade-off matrix |
| 3 | `exec-advisor` | options + matrix | BLUF + recommendation + ask |
| 4 | `/5gate` | full proposal | review findings |
| 5 | `/wrap-up` | reviewed proposal | `proposal.md` + memory captures |

### proposal.md frontmatter

```yaml
arc: proposal
slug: <slug>
upstream: research/<slug>/findings.md
created: <ISO8601>
options: 3
recommendation: <option-id>
status: awaiting-approval | approved | rejected
```

---

## Arc 3: build

**Spine (v5, historical):** `/kickoff → /kickoff [plan-only] → /architect → /5gate-build-comms (pre-build) → /build → /test → /5gate-build-comms (post-build) → deploy → /wrap-up`
**Execution (v6):** `src/chimera/arcs/build.py`
**Output artifact:** `tasks/<task-id>/artifacts/` — `manifest.md` + specialist build records

Note: `/plan` is deprecated. Plan-only mode is activated via `[plan-only]` token in `/kickoff`
(per ADR-001 rev2, reconciled from SPEC v1.0 in this v1.1 update).

### Step contract

| Step | Skill | Reads | Writes |
|---|---|---|---|
| 1 | `/kickoff` | approved proposal | implementation context + `_seed.md` |
| 2 | `/kickoff [plan-only]` | impl context | `task_plan.md` (numbered task plan) |
| 3 | `/architect` | task plan | refined design + risk surface + `_design.md` |
| 4 | `/5gate-build-comms` (pre) | design | go/no-go gate |
| 5 | `/build` | greenlit plan | implementation files + `_progress.md` |
| 6 | `/test` | implementation | test results |
| 7 | `/5gate-build-comms` (post) | tests + impl | quality verdict |
| 8 | deploy | greenlit impl | deploy artifact (per project) |
| 9 | `/wrap-up` | full build | `manifest.md` + memory captures |

### manifest.md frontmatter

```yaml
arc: build
slug: <slug>
upstream: proposal/<slug>/proposal.md
recommendation_applied: <option-id>
files_created: [<path>, ...]
files_modified: [<path>, ...]
external_references: [<repo-or-url>, ...]
tests_run: <bool>
deploy_target: <target-or-null>
status: complete | partial
```

---

## Arc 4: comms

**Spine (v5, historical):** `/kickoff → exec-advisor → /architect (structure) → /writing-styles → /5gate-build-comms → /wrap-up`
**Output artifact:** `tasks/<task-id>/artifacts/digest.md` (or `deck.md` / `brief.md`)
**Execution:** `src/chimera/arcs/comms.py` (shipped 6.4.0, retired v7)

### Step contract

| Step | Skill / agent | Reads | Writes |
|---|---|---|---|
| 1 | `/kickoff` | build manifest.md + audience | comms scope + `_seed.md` |
| 2 | `exec-advisor` | scope | BLUF + key messages |
| 3 | `/architect` | messages | document structure + `_design.md` |
| 4 | `/writing-styles` | structure | drafted body in chosen voice |
| 5 | `/5gate-build-comms` | full draft | review findings (voice + audience gates) |
| 6 | `/wrap-up` | reviewed draft | `digest.md` / `deck.md` / `brief.md` + memory |

### Output frontmatter

```yaml
arc: comms
slug: <slug>
upstream: build/<slug>/manifest.md
audience: internal | stakeholder | exec
voice: <style-name>
created: <ISO8601>
status: complete
```

---

## Chain Rules

1. **Read only your declared upstream.** No cross-arc reads. An arc that
   needs information from a non-adjacent arc must have that information
   surfaced into the adjacent arc's output schema first.
2. **Outputs are immutable once `status: complete`.** Re-runs create a
   new slug (e.g., `<original-slug>-v2`).
3. **Schema failures halt the chain.** A downstream arc that finds an
   invalid upstream emits a structured error at
   `flows/<arc>/errors/<slug>.md` instead of guessing.
4. **Slugs are stable across arcs.** The build arc must use the same
   slug as the proposal arc that approved it.
5. **No state outside frontmatter.** Arcs that need to pass state to
   the next arc do so via frontmatter fields, not side channels.
6. **Skill spines are fixed.** The skill sequence per arc is canonical.
   To change it, edit this SPEC and bump arc version.

## Arc version

| Arc | Version | Last revised | Execution (v7) | Notes |
|---|---|---|---|---|
| research | 2.0 (v6) | 2026-06-10 | ⛔ retired v7 (was `src/chimera/arcs/research.py`) | workflow.js v2.1 port; parity pinned by `tests/test_research_arc.py` |
| design | 0.1 (v6) | 2026-06-16 | ⛔ retired v7 (was `src/chimera/arcs/design.py`) | single-deliverable iteration runbook; 10 sanitized prompt templates (7 makers + 2 critics + publisher); parity pinned by `tests/test_design_arc.py`. v0.2 (multi-deliverable + set-consistency) on first real need |
| proposal | 1.0 (v6) | 2026-06-17 | ⛔ retired v7 (was `src/chimera/arcs/proposal.py`) | research findings → decision-ready proposal |
| build | 1.0 (v6) | 2026-06-17 | ⛔ retired v7 (was `src/chimera/arcs/build.py`) | approved proposal → record-only build manifest |
| comms | 1.0 (v6) | 2026-07-01 | ⛔ retired v7 (was `src/chimera/arcs/comms.py`) | shipped 6.4.0; upstream artifact to audience-shaped writing via a soft voice_gate |
| n8n | 0.1 (v6) | 2026-07-01 | ⛔ retired v7 (was `src/chimera/arcs/n8n.py`) | shipped 6.4.0; record-only n8n workflow arc (never publishes); parity pinned by `tests/arc_drivers.py` |
| reflect | 1.0 (v6) | 2026-06-23 | ⛔ retired v7 (was `src/chimera/arcs/reflect.py`) | v6-core off-critical-path self-improvement; G2-gated, never self-applies |
| gemini | 1.0 (v6) | 2026-07-16 | ⛔ retired v7 (was `src/chimera/arcs/gemini.py`) | shipped 6.6.0; record-only Gemini Enterprise build manifest; parity pinned by `tests/arc_drivers.py` |
