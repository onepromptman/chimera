# Project: chimera (v7)

Git-durable background task engine driven by Claude Code cloud sessions on
the operator's subscription. The package (`src/chimera/`) is the deterministic
skeleton; a cloud session is the runtime. Boris methodology: loops > prompts,
externalized memory, adversarial verification, maker ≠ checker. Owner: onepromptman, AI
builder — personal machine, personal subscription (Security Rule 1).

**Start here:** `README.md` (quick start) → `TICK_PROTOCOL.md` (how a session
drives one task) → the module docstrings. Architecture decision records,
the backlog and audit reports are maintained in the maintainer's private
repository and are not shipped in this public copy (see `decisions/README.md`).

## Security Rules

1. **Push boundary (load-bearing).** Work content never enters this repo:
   not the public mirror, not the private origin, not a branch. Protection is
   gitignored zones + the warn-only push-scope hook
   (`.claude/hooks/push-scope-advisor.py`) + an optional untracked pre-push
   guard where installed. Known residual: path-shaped (ADR-006). One repo, one
   purpose: work aimed at another repo runs in THAT repo's checkout.
2. Never commit `.env`, `.mcp.json`, credentials, tokens, API keys. The
   package holds no tokens. `.g2-operator.sha256` is a digest, not a
   credential; the G2 token itself lives only on the operator's machine.
3. Always HTTPS; OAuth stays with the session; never hardcode.
4. Validate input at trust boundaries with Pydantic (`models.py` is the single
   contract surface; every model is `extra="forbid"`).
5. Never `eval()`/`exec()`/`pickle.loads()` on untrusted data.
6. Never `yaml.load()`.
7. Never `subprocess.run(shell=True)`; commit-message text is sanitized.
8. Minimal supply chain: direct deps pinned exactly (pydantic 2.13.4, pytest
   9.0.3), the full closure locked in `requirements.lock`, Actions pinned by
   SHA. (This is a supply-chain choice, not an export-control requirement.)
9. Nothing prints a secret today; if a surface ever does, mask to the last 4.

## Invariants (numbers here, mechanics in the docstrings)

| Invariant | Value | Where |
|---|---|---|
| Human gates | G1 is the operator authoring the ask at `chimera new`; ambiguity is clarified in-session before submission, not via an async question branch (removed 2026-09-16: zero `questions.yaml` in 550+ commits). G2 is review-after: a passing panel auto-approves (`approved_by: auto:verify-pass`) and the task goes to `done` without parking; a failing panel parks at `awaiting-signoff`; `approve`/`reject` require the operator token and read the committed digest, not the working tree | `cli.py` (submit), `gates.py` |
| Queue | 6 states; every transition is a commit; `done` only via `transition()` (verify gate + G2) | `queue.py` |
| Arc surface | ONE: `graph` (planner-emitted DAG; the DAG is data, loops are code); retired arcs load, never dispatch; their craft lives in `decisions/references/` | `arcs/graph.py`, ADR-009/010 |
| Levers (default / hard max) | width 3/8 · phases 5/10 · repair laps 1/3 · mutant cadence 5/(1..50, never off); a typo reads as unset | `levers.py` (the only reader of the graph levers; model levers in `agents.py`) |
| Models | maker `opus` · critic `sonnet` · research `sonnet` · judge = maker; call-time; maker ≠ checker refused at admission. Effort is pinned per role in `ROSTER` and emitted as frontmatter — planner/judge `xhigh`, maker/critic `high`, researcher `medium`, executor `low`; unset inherits the session, and no role requests `max` (not available on every tier) | `agents.py`, `graph.node_model` |
| Verification | 2-critic REFUTE, fatal-only veto: GREP-ANCHOR critic + executing CONFIRM on `executor`. `valid < 2` fails (too thin); a refuted `fatal` vetoes alone; every non-fatal refutation is recorded in `VerifyResult.open_findings` and rides the digest instead of blocking (80/20 made structural). The proportional-majority rule was removed 2026-09-16 on the frozen run record (`tests/data/`: four panels, 12 committed opinions, 0 graded `fatal`, 3 runs failed on material-only refutations whose authors wrote the conclusion survives — summed by `tests/test_verify.py`); `VerifyResult.advisory_count` still records `cosmetic` refutations for the digest only. One bounded critique→rewrite lap unchanged; executor angle unchanged. Every 5th verdict (`CHIMERA_VERIFY_MUTANT_CADENCE`, cadence derived from the count of committed `tasks/*/verification.json`) also dispatches one `verify:mutant1` control against a locally seeded single-defect variant of the artifact; the control never enters `opinions` and changes no verdict — it writes `VerifyResult.panel_credential` (`panel-uncredentialed` \| `mutant-caught` \| `mutant-missed` \| `mutant-not-measured`) and `tasks/<id>/mutants/run-<n>.json`. Any mutant failure degrades to `mutant-not-measured`, never a block | `verify/lite.py`, `verify/mutants.py`, `arcs/graph.py` |
| Ceilings | 250 calls per run; 300 s per call, 1800 s for plan/node/wrap; wrap body 94,000 chars (`models.WRAP_BODY_MAX`) — refused at the schema gate above it, the maker told the number up front and re-run once with the overage named, so the verify panel's 96,000-char window (`VERIFY_PAYLOAD_MAX`, ceiling + render headroom) sees every gated artifact whole; truncation past the window stays marked, never silent | `runner.py`, `arcs/graph.py`, `models.GraphArtifact` |
| Roles | six fences over four grants; write+shell and write+network unconstructible | `roles.py` |
| Checkers | see exactly {ask, rubric, read artifacts}; derive a model distinct from the producer's dispatched model | `graph.checker_brief`, `arcs/graph.py` `GraphArcState.node_models` |
| Memory | arcs APPEND an L2 run abstract at terminal phases (`run-summary-<n>`: ask, shape, BLUF, what the panel refuted, tallies); the priors seed reads L2 (FTS then recency) plus L1 feedback/project/pattern rows, every read separately guarded and fail-open, block ≤ 1,500 chars; the seed rides the PLANNER call alone (it authors the node briefs) — no worker or wrap prompt carries it, and checkers never see it; rows tagged `decision` rank first on every read (search CLI, `search_general`, priors block) | `arc_memory.py`, `_common.priors_block`, `graph.run_abstract`, `memory.decision_first` |
| Build mode | Proposed: target repo + diff artifact + confirm role + PR as artifact + the record-sanitization boundary (§(e).2, load-bearing: no target content in `tasks/<id>/` beyond diff/stat/PR, and no diff at all for a corp target), proven first as `/chimera-build-mode` | ADR-011 |

Every row above has a test; a doc claim without a test is a bug (Rule 6 below).

## File organization (fences are load-bearing)

| What | Home |
|---|---|
| ADRs / plans / audits | `decisions/ADR-NNN-*.md` (sequential, never reuse) · `decisions/plans/` · `decisions/audits/YYYY-MM-DD-topic.jsonl` (the `router-audit-*` prefix is retired history, never a live series) |
| Craft references (human-read, not shipped) | `decisions/references/` |
| Role subagents | rendered from `agents.ROSTER` by `chimera install-agents` (six files) |
| Skills / hooks / workflows | `.claude/skills/*/SKILL.md` · `.claude/hooks/` · `.claude/workflows/` |
| Checkpoints | `wip/checkpoints/` (gitignored, keep last 3) |
| Personal/work content | gitignored zones only — never tracked, never on a branch |

Don't rename `flows/` (a `.gitignore` anchor). Keep `decisions/` names stable.

## Behavioral Rules

1. **Durable state first.** Commit every transition/artifact immediately;
   push fast-fails on permanent errors; `chimera status` shows push health.
2. **Intake is in-session.** Ambiguity in the ask is clarified before
   `chimera new` runs; there is no async question-and-park branch.
3. **Arcs ship only via `tests/arc_drivers.py::ARCS`** and green parity suites.
4. **80/20.** Flags below confidence 70 ride the digest; they don't block.
5. **Root cause before fixes; plan before execute** (3+ files → numbered plan).
6. **Docs trail implementation.** No document may claim an invariant with no test.
7. **One repo, one purpose.** Chimera worktrees and branches are for framework
   work only; they are removed when merged.
8. **Prove before infra.** Run a real task through G2 before hardening more.
9. **Fail-open memory.** Every memory write and read on a terminal path
   (archive capture, PASS summary, FAIL row, priors seed) degrades rather
   than blocking; `chimera archive` reports `memory_captured`, a degraded
   PASS write logs `MEMORY_WRITE_DEGRADED` to the arc log.

## Commands

`python -m chimera --help` is canonical. `python -m pytest -q` (single test:
`python -m pytest tests/<file> -k name`). CI gates: `ruff check src tests
scripts .claude`, `ruff format --check src tests scripts .claude`, `mypy src`,
`pip check`, and every pyproject pin present in `requirements.lock`; run them
as `python scripts/gates.py` (`--no-tests` for the static ones), which refuses
tool versions that differ from the pins — full version strings, so a
`.post1`/`rc1`/`+local` build is a mismatch (`pre-commit run --all-files` is
the hook form). `chimera install-agents` writes the six role files.
`chimera init` is the first-run wizard; `init --check` is its preflight alone
(writes nothing).
`chimera g2-token init|check` manages the operator token. `python
scripts/chimera_memory.py search <q>` is FTS5 over the memory DB; `prune
--older-than-days N [--type T] [--dry-run]` ages it out (`feedback`/`user`
exempt unless named); `synthesize --older-than-days N [--dry-run] [--agent
NAME] [--type T] [--export PATH] [--no-backup]` clusters arc rows by month,
≥2 members, parts of ≤4,000 chars, then VACUUMs
(`feedback`/`user`/`decision`/`digest` never candidates); read commands refuse
an absent DB instead of creating one.

## Notes

- Dispatcher: a cloud Routine runs `chimera tick` on a schedule (one worker
  at a time — TICK_PROTOCOL doctrine); `/loop chimera tick` for interactive days.
- G2 moves to a GitHub PR review once build mode lands (ADR-011); the token
  is the interim.
- Ad hoc CLI runs outside pytest write the REAL `~/.chimera/memory.db` on
  archive; set `CHIMERA_DB_PATH` first when experimenting. WSL: chmod 0600 is
  a drvfs no-op.
- Router hook is a counter: it logs which subagent type was requested, never
  prompt text, imports nothing from the package, and always allows.
  `routing.py` is now only `validate_selection` + `registry_from_agents`.
- `TaskSpec` no longer carries the nine legacy per-arc fields;
  `queue.REMOVED_SPEC_FIELDS` still guards them at load so old records keep
  loading (the one-shot `migrate-tasks` CLI command that stripped them is
  gone, completed 2026-07-02). `target_repo` returns as a real field with
  ADR-011 (drop it from `queue.REMOVED_SPEC_FIELDS` in that change).
- A stranded retired-arc `ready` record has no queue-level exit (D3, handoff
  item 3): the operator clears one by hand with `git rm -r tasks/<id>` +
  commit, not a queue verb.
