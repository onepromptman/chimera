# Research Arc — Runbook (HISTORICAL, retired in v7)

> **Retired 2026-08-28 (ADR-010).** The `research` arc no longer dispatches;
> `src/chimera/arcs/research.py` and `tests/test_research_arc.py` were
> deleted, and `research` now appears only in `models.RETIRED_ARCS`. This
> runbook is kept as the reference description of the judge-panel *shape* —
> the planner composes it as data on the one live arc (`graph`). Nothing
> below is a live execution surface.

**Original execution surface (retired):** `src/chimera/arcs/research.py` —
the workflow.js v2.1 Boris judge-panel composition, ported to a resumable
state machine driven by a cloud session (see `TICK_PROTOCOL.md`).

```
Scope (3-5 sub-Qs)
  └─ per sub-Q:
       Plan        3 DISTINCT investigation plans (different angles)
       Investigate 3 investigators in parallel (Opus); each verified by a
                   3-critic REFUTE panel (Sonnet); proportional-majority survival
       Judge       2 judges; 3rd tiebreaker on disagreement; array-order fallback
       Synthesize  winner finding + grafts from runners-up
  └─ Review        goal-reviewer scores coverage; ≤1 gap-fill round (MAX_LOOPS=2),
                   gap-slug collision guard
  └─ Wrap          findings.md + result.json under tasks/<id>/artifacts/
  └─ Verify        lite 3-critic REFUTE of the findings artifact
```

## Invocation

```bash
python -m chimera new "<question>" [--context "..."]
python -m chimera tick          # claim + get pending agent calls
# run each call with the session's Agent tool, then:
python -m chimera arc submit <task-id> <label> --json '<result>'   # or --null
```

The arc quiesces at `awaiting-signoff` with the digest updated and a
sign-off comment payload for the task's Issue. `chimera approve` (G2) →
done → `chimera archive`.

## Return shape (tasks/<id>/artifacts/result.json)

- `syntheses[]` — consolidated winner findings per sub-Q (winner_angle,
  grafted_from, confidence, judge panel telemetry)
- `candidates_for_diff[]` — all verified candidates so the operator can override
  the judge's pick
- `goal_review` — coverage + load-bearing/decorative + recommended next_arc
- `failed_sub_questions[]` — drop reasons (plan-rejected,
  all-investigators-null, all-candidates-refuted, synth-null)
- `metrics` + `audit_trail` — counts, rounds, null/timeout/threw counters,
  ceiling (250) and per-branch timeout (300s)

## Hardening (enforced by `runner.py` + the arc)

per-branch timeout 300s · agent-call ceiling 250 · logged null-degrades
(never raises) · proportional critic-majority (≥2 valid AND strict majority
unrefuted) · gap-slug collision guard · maker ≠ checker asserted before any
critic panel fires.
