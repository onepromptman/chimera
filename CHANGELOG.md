# Changelog

All notable changes to chimera are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR** — incompatible change to the queue state machine, on-disk
  artifact formats, or the tick protocol contract.
- **MINOR** — backward-compatible capability (a new arc, new specialist
  wiring, new CLI surface).
- **PATCH** — backward-compatible bug fix or hardening.

The public mirror (`onepromptman/chimera`, not `chimera_v1` — a private secondary twin) tracks these releases. To find out whether a
newer framework version is available, compare your `pyproject.toml` `version`
against this file's top entry (`pyproject.toml` + this file are canonical;
git tags currently lag behind and should not be trusted for version checks).

## [Unreleased]

Memory synthesis: the store compacts toward decisions instead of accumulating
telemetry. Then a lean pass — the prompt skeleton is sent once instead of per
call, the one-caller arc spine folded into the arc that calls it, and the dead
surface deleted.

### Added

- **`synthesize` on the memory CLI** (`python scripts/chimera_memory.py
  synthesize --older-than-days N [--dry-run] [--agent NAME] [--type T]
  [--export PATH] [--no-backup]`): rows past N days cluster on four
  STRUCTURAL facts — agent, type, arc_kind, and the `YYYY-MM` month of
  `updated_at`. Nothing is parsed out of a title, so a row whose trailing
  number is its identity (ADR-006 vs ADR-009) is never merged with another,
  and a bucket never grows past one month of one arc kind. A cluster of two
  or more collapses into one `digest`-tagged row per PART: parts chunk the
  member lines at 4,000 chars so every member appears in exactly one part and
  none is ever dropped. A digest keeps its members' agent/type/arc_kind (no
  new type, no schema migration), carries the union of their tags, and is
  titled `digest:<agent>|<type>|<arc_kind>|<YYYY-MM>:part-<k>`.
- **Only `arc` rows are candidates** unless `--agent NAME` (repeatable) opts
  another agent's rows in — synthesis compacts what the framework wrote, not
  what the operator did. A cluster of ONE is left untouched and reported as a
  skipped singleton: a digest of a single row is bigger than the row.
- **Three absolute exemptions**: `feedback`/`user` rows (as in `prune`, but
  `--type` cannot override them here), rows tagged `decision`, and rows tagged
  `digest` — the last is the dedup rule, so a second `synthesize` over an
  already-synthesized store is a no-op.
- **A write path that can be undone**: the snapshot is
  `<db>.<YYYYMMDD-HHMMSS>.bak` and never overwrites an earlier one (unless
  `--no-backup`); one transaction inserts the digests and deletes the members,
  and any sqlite error rolls it back and exits 1 with the snapshot named
  instead of raising. `--dry-run` performs ZERO writes — not even a schema
  ensure — so a legacy-schema store is byte-identical afterwards.
- **`--export PATH`** writes a markdown digest of the whole store after the
  operation (or of the planned state under `--dry-run`): decisions first, then
  digests, then one line per row grouped by type. With no value it defaults to
  `wip/memory-digest-<YYYY-MM-DD>.md`, inside the gitignored `wip/` zone, so
  an export can never become a tracked file.
- **Seeded-mutant panel credential** (candidate C15 of the clean-sheet run's
  first design synthesis, kept in
  `tasks/20260916-clean-sheet/arc-state.failed-20260916T190608Z.json`;
  `src/chimera/verify/mutants.py`): every 5th verdict
  (`CHIMERA_VERIFY_MUTANT_CADENCE`, range 1..50; `0` or a typo reads as the
  default, so the control cannot be switched off through the environment; the
  cadence is derived from the count of committed `tasks/*/verification.json`,
  no counter file) also dispatches ONE control call, `verify:mutant1`,
  carrying a copy of the wrap artifact with exactly one deterministic defect
  seeded by code, never by a model: a cited path that exists made unfindable
  (M1 `path-anchor`, GREP-ANCHOR angle), the first Open-findings bullet
  deleted (M2 `open-finding`, CONFIRM angle), or the first body quantity
  shifted to `n*7+1` (M3 `quantity`, CONFIRM angle), under a prompt
  byte-identical to the real angle's. The control's opinion never enters
  `opinions` or `open_findings` and never moves `passed`; it lands on
  `VerifyResult.panel_credential` (`panel-uncredentialed` | `mutant-caught` |
  `mutant-missed` | `mutant-not-measured`), on `VerifyResult.mutant`, and in
  `tasks/<id>/mutants/run-<n>.json`; the digest, the signoff comment and the
  L2 run abstract disclose it. Every failure path — no operator applies, the
  defect falls past the panel's payload window, a null, a timeout, a
  malformed payload, a stray submit on a run that scheduled no control —
  reads `mutant-not-measured`, and a repair lap never re-dispatches one.
  Cost: one extra call on one run in five, disclosed to the planner in its
  machinery count. A caught mutant is a floor-raiser, not a certificate: it
  says the panel caught a SEEDED defect of a SEARCHED class, so every surface
  says "caught", never "verified", and the credential never gates
  (`mutant-missed` still auto-approves a PASS). Additive on the record: both
  new fields default, so every committed `verification.json` and
  `arc-state.json` still loads.

### Changed

- **The wrap body ceiling is a schema gate, and the panel window is wider.**
  Telling the maker the ceiling (below) was not enough: every wrap of the two
  2026-09-16 runs — four artifacts, 52,916 to 57,171 characters — overran a
  46,000 ask, and each panel graded an artifact cut at 48,000. The ceiling now
  lives on the contract surface as `models.WRAP_BODY_MAX` (94,000) with a
  validator on `GraphArtifact.body`, so an over-long wrap is refused at `arc
  submit` before anything is written, with an error that names the overage
  ("cut N chars of restatement, never evidence or open findings") and rides
  the driver's one retry; `VERIFY_PAYLOAD_MAX` is derived as that ceiling plus
  `WRAP_BODY_HEADROOM` (96,000), sized for frontier-tier critics, so a gated
  artifact always reaches the panel whole and the marked truncation is a
  fallback nothing gated can reach. Every committed artifact body (largest
  57,171) loads under the gate, which a test walks. Tests:
  `test_a_wrap_over_the_body_ceiling_is_refused_at_the_gate_and_state_survives`,
  `test_a_body_at_the_ceiling_reaches_the_panel_whole`,
  `test_every_committed_artifact_body_is_under_the_wrap_ceiling`.
- **The run-record tally behind the fatal-only veto is pinned to the frozen
  fixtures.** Six live surfaces (`CLAUDE.md`, `verify/lite.py` twice,
  `graph.py`, `tests/test_verify.py`, `tests/arc_drivers.py`) asserted "4
  runs killed on material-only refutations"; only three of the four
  lite-verify kills in git left a verification record (the 2026-07-02 reflect
  run has no severities on disk), so every site now cites what `tests/data/`
  holds — four panels, 12 committed opinions, 0 graded fatal, 3 FAIL records
  — and `tests/test_verify.py` sums those fixtures, greps every live surface
  for the retired wording (a hand-kept list of sites missed one), and asserts
  `graph._VERIFY_PANEL == len(lite.ANGLE_ROLES)`, which a comment had claimed
  and nothing enforced.
- **The role prelude ships once, in the installed role file.** Every prompt
  builder re-authored the role identity, its discipline and the output-contract
  SHAPE on every call, while `agents.ROSTER` already installs a per-role system
  prompt that `subagent_type` dispatches to — the role was described twice, per
  call, forever. The static text moved into `ROSTER`; a brief now carries only
  call-specific content and NAMES the schema it owes. Per call: planner
  2,618 → 408 chars, worker node 460 → 142, checker 2,165 → 1,640, wrap
  2,569 → 2,019, each verify critic ~549 → ~488. The six installed role files
  grow 2,375 → 7,278 chars in total, once.
- **The `repo` test fixture copies a session template** instead of building a
  git repo (and a bare origin) from nine subprocesses per test. ~75 tests take
  it; measured here at 48 ms → 13 ms each. Same head, same tests: the git-heavy
  files (70 tests) go 13.3–14.3 s → 9.6–10.4 s and the whole suite 20.0–22.3 s
  → 16.8–17.6 s. Each test still gets its OWN copy of both repos with the
  remote repointed at its own bare origin, and a test pins exactly that.
- **The priors seed rides the planner call alone.** The 1,500-char L2 block
  was spliced into every worker-node brief as well — up to 15 byte-identical
  copies per run at the default levers (1,502 chars each). The planner writes
  the node briefs, so what matters travels folded into them; `worker_brief` /
  `node_call` / `phase_calls` lost the `priors` parameter. Checkers never
  carried it and wrap never did.
- **Decision-first ranking** (`memory.decision_first`): a row whose `tags`
  carry the whole-word token `decision` sorts ahead of every other hit, with
  the relative order of both groups preserved. Applied in the `search` CLI, in
  `search_general` (the programmatic layer-1 read), and in
  `arcs/_common.priors_block`, so an operator-tagged decision survives the
  seed's 1,500-char cap ahead of run telemetry. Ranked reads over-fetch before
  the re-rank, so a decision just outside the bm25 window is not lost to
  `--limit`.
- **The layer-1 FTS tokenizer is `\w{3,}` (unicode)**, not `[A-Za-z0-9]{3,}`:
  a CJK or accented ask tokenized to nothing, so `fts_or_query` returned "no
  query" and the priors seed never reached layer 1 for it. Quoting is
  unchanged — every token is still matched as a literal.
- `backup` now shares one `snapshot_db` implementation with `synthesize`.
- **A refused plan comes back to the planner.** The re-plan lap fed back only
  chimera's refusal string, so the most expensive call in the run re-derived a
  shape it had already emitted. The refusal now carries the refused plan's
  skeleton — phases, node ids, roles (`plan_skeleton`) — and the planner diffs
  instead of guessing.
- **Verify panel: 2-critic REFUTE, fatal-only veto, with `open_findings`.**
  The 3-critic proportional-majority rule (`unrefuted > valid/2`) is retired:
  `valid < 2` still fails a panel that is too thin, a refuted `fatal` vetoes
  alone, and every non-fatal refutation now lands in
  `VerifyResult.open_findings` to ride the digest instead of blocking (80/20
  made structural). Evidence on the run record (frozen in
  `tests/data/`): four panels, 12 committed opinions, 0 graded fatal, 3 runs
  failed on material-only refutations whose own authors judged the conclusion
  survived — a severity gate over the proportional rule was
  tried and removed for the same reason (identical verdicts at n=3, and its
  exemption opened a tie hole at n=4). Panel call overhead drops 10 → 7.

### Removed

- **`decisions/arc-handoffs/` (1,656 lines).** Six build briefs for arcs
  ADR-009/010 retired — how to implement proposal, build, comms, gemini and
  the memory layers, against a `src/chimera/arcs/` that no longer exists. The
  craft worth keeping already lives in `decisions/references/`; the briefs
  themselves stay in git history. `flows/SPEC.md` and the README tree no
  longer point at them.
- **`arcs/_common.py` as a shared spine.** It was written for eight arcs; one
  remains, and every helper in it had exactly one caller. `arc_save`,
  `arc_load`, `read_verify_result`, `dispatch_null`, `stamp_first_issued`,
  `gate_critic_opinion` and `load_task_record` are now the arc's own code
  (several of them inline), and the four-hop verify finalize (`_submit_verify`
  → `accumulate_verify_opinion` → `finalize_verify_with_repair` →
  `finalize_with_repair`, one caller each) is ONE function on the arc. What is
  left in `_common.py` is what more than one module reads: `ARC_STATE_FILE`
  and the `priors_block` seed. `arcs/` is 267 lines lighter.
- **`GraphArc.submit`'s dual-form branch.** The untyped
  `submit(label, payload)` string-first form had no caller — not the CLI, not
  one of the 100+ test call sites — and cost the signature its types. The
  parameters are now typed `(state: GraphArcState, label: str, payload)`.
- **Dead and test-only code, with its tests and doc mentions**:
  `notify.digest_comment` (0 callers; called dead by the 2026-07-02 audit and
  still shipped), the `agents.socratic()` / `agents.simplifier()` lens prompt
  builders (0 callers, independent of the G1 three-outcome branch and its
  `--outcome` flag — both since removed outright, see below),
  `models.LAUNCH_ARCS` (0 readers), `models.StepOutput` (in the
  registry, emitted by no arc), `verify.schema_gate.validate` (production
  routes everything through the typed `validate_as`), `GraphArc.initialize`
  (the CLI starts an arc with `start`), `RoleFence.has_shell` /
  `RoleFence.has_network` / `roles.distinct_grants` (asserted only by their own
  tests), and `arc_memory.summarize_run` (the fixed-title writer the terminal
  paths stopped using when `append_run_summary` landed).
- Exec-repair machinery, `graph.judge_tier_warning`, and the call-budget
  lever (`levers.py`): dead surface once the 2-critic panel above stopped
  needing them.
- **MAJOR: the queue state machine loses `awaiting-input` and its one
  conditional edge.** `ready → archived` for a retired-arc record
  (`queue.CONDITIONAL_TRANSITIONS`, P1-11) is gone outright, not widened —
  the queue is left with 6 states and zero conditional edges. A stranded
  retired-arc `ready` record now has no queue-level exit at all; the
  operator clears one by hand with `git rm -r tasks/<id>` + commit, then
  re-opens the ask as a graph task if it still matters.
- **MAJOR: the G1 async ask/answer branch, and the `chimera answer` CLI
  verb with it.** `chimera new` no longer takes an outcome that parks the
  task — an ambiguous ask is clarified in conversation before the call (D1,
  handoff item 3: zero `questions.yaml` files across 550+ commits). Also
  gone: `gates.g1_answer`, `write_questions`/`read_questions`,
  `AskOnceViolation`, `IntakeQuestion`/`IntakeQuestions`, and the now
  unreachable parallel-ab outcome and `SocraticOutcome` type —
  `g1_intake` is single-path.
- **MAJOR: the `chimera migrate-tasks` CLI verb.** Zero observed uses after
  its 2026-07-02 run (D2, handoff item 3). `Queue.migrate_task_records()`
  remains — it is the only unwedge path for a pre-schema-change record — but
  it is now called directly, not through the CLI.

### Fixed

- **First-issue stamps are committed, not just saved.** `chimera tick` and
  `chimera arc next` stamped `first_issued` into `arc-state.json` and saved
  it, but only `arc submit` followed the save with a checkpoint, so the
  stamp died with the container and a re-issued call restarted its timeout
  clock (run `20260904-tick-stamp-durability`, defect A). Both verbs now
  checkpoint after the issuing save; a quiet resume tick adds no commit
  because `gitio.commit` is a no-op when nothing changed.
  `test_tick_commits_the_first_issue_stamp`,
  `test_quiet_tick_adds_no_commit`, `test_arc_next_commits_a_restored_stamp`.
- **`approve`/`reject` take the writer lock; the POSIX wait is bounded.** The
  G2 verbs committed through the same git index a running tick uses with no
  lock, so a same-checkout race lost the approve's commit and left the
  working tree reading `done` while history read `awaiting-signoff`
  (defect B). They now take `tick_lock(wait=True)`; and because the POSIX
  wait branch blocked forever, both platforms now share one bounded poll
  (`queue.LOCK_WAIT_S`, 60 s) that raises `QueueError` on timeout.
  `test_g2_verbs_take_the_writer_lock`,
  `test_approve_waits_for_a_held_lock_and_still_commits`,
  `test_wait_mode_times_out_on_a_stuck_holder`.
- **The wrap maker is told the verify panel's body ceiling.** The panel reads
  at most `VERIFY_PAYLOAD_MAX` (48,000 at the time) characters of the rendered artifact
  and marks the cut, but nothing told the maker that cap existed: the first
  real run's 52,157-character wrap lost its Open findings to the cut and a
  critic refuted the lap for what it could not see. The wrap prompt now
  carries a `BODY CEILING` line net of `WRAP_BODY_HEADROOM` (2,000 for
  frontmatter and render overhead); `test_wrap_maker_is_told_the_panel_body_ceiling`.
- **G2 was self-served (P1-1).** `gates.g2_enforcement` and
  `gates.verify_operator` read `.g2-operator.sha256` from the WORKING TREE,
  which the worker session writes on every transition: `rm
  .g2-operator.sha256` flipped enforcement to `none` and `approve` succeeded
  with no token, and overwriting the file with the digest of an
  attacker-chosen token made that token the operator's — neither edit is
  staged by `gitio.commit`, so restoring the file left no trace. Both now read
  the COMMITTED digest (`git show HEAD:.g2-operator.sha256`, via the new
  `gitio.show_at_head`). The working-tree file is honoured only before its own
  first commit, so a rotated-but-uncommitted digest is not trusted.
- **`g2-token init --token-path` inside the repo (P2-1)** wrote the plaintext
  token into the tree while the CLI printed that it was stored outside.
  `init_operator_token` now refuses a path under the repo root, before writing
  anything, and the CLI note says what the code enforces.
- **The push-scope advisor missed `git -C <path> push` (P2-2)**: the regex
  required `push` immediately after `git`, so the check was skippable by
  spelling the command differently. It now matches any git invocation that
  also says push.
- **`scripts/gates.py` accepted `0.9.6.post1` / `0.9.6rc1` / `0.9.6+local` as
  the 0.9.6 pin (P2-3)** — a three-number capture over `--version` output threw
  the suffix away. It reads `importlib.metadata.version()` and compares full
  version strings; `--version` is a fallback only. The runner also gained
  argparse (`--no-tests`, `-h`), `pip check`, a gate asserting every pyproject
  pin appears in `requirements.lock`, and lint over `scripts` and `.claude`.
- **`claude-review.yml` granted `pull-requests: read` / `issues: read` (P2-4)**
  to steps whose only job is posting inline and issue comments, so the posts
  failed and the review was a silent no-op. Both are `write`; `contents` stays
  read and the allowedTools list keeps the token to comments.
- **Push-scope telemetry (P3-1, P3-2)**: the record now goes through an
  explicit `_FIELDS` allow-list with 10 MB rotation (mirroring
  `router-enforcer.py`), and the 500 KB check sizes the blob at HEAD
  (`git ls-tree -r -l -z HEAD`) instead of stat-ing the working tree, so a file
  committed large and truncated on disk is still what the push carries.
- **Router telemetry**: `session_id`, `subagent_type` and `model` were copied
  uncapped; a marker planted in any of them reached the log. Each is truncated
  at 64 chars.
- **CI**: lint covers `src tests scripts .claude` (pre-commit sees every file,
  so anything CI skipped was a red hook on a clean tree — an unused import and
  four unformatted files), the pin/lock agreement is a real step next to the
  honestly renamed `pip check`, concurrency keys on the PR number so a PR stops
  running the matrix twice, and the header no longer claims CI resolves nothing
  at run time (PEP 517 fetches the pinned build backend).
- **Tests for claims that had none**: the G2 compare is pinned to
  `hmac.compare_digest`, and an `ast` walk asserts both hooks import stdlib
  only — which CLAUDE.md asserted and nothing checked.
- **The `tasks/` tripwire contradicted the engine it guards.**
  `test_no_task_records_are_tracked` asserted `git ls-files tasks/` was empty,
  but the queue is git-durable — every transition commits `tasks/<id>/…` to
  the private origin (Behavioral Rule 1, `TICK_PROTOCOL.md`) — so the first
  real task turned CI red on its first commit and the dispatcher's `pytest -q`
  preflight would have refused to work while any task existed. Replaced by
  `test_tracked_task_records_are_engine_state_only`, which pins what the
  2026-09-02 audit actually found (P0-2/P1-4: an orphan directory of data with
  no task record, not queue state): every tracked path under `tasks/` must sit
  in a directory whose `task.json` loads as a `TaskRecord`, carry a basename
  the CLI writes (derived from `queue.Queue`, `gates.QUESTIONS_FILE`,
  `arcs._common.ARC_STATE_FILE`, `arcs.graph.ARTIFACT_FILE`), and stage under
  256 KB — the arc state excepted, since it accumulates every node's output
  (79 KB three nodes into the first live run) and a byte ceiling on it would
  be the same bug again. A negative test plants an orphan data drop, an
  oversized artifact and an unloadable record in a throwaway index to prove
  the check fires, with a fat arc state as the control it must let through.

## [7.2.0] — 2026-09-02

The second framework audit (`decisions/plans/2026-09-02-framework-audit.md`,
scores C7 F7 Q7 B4 U4 A5 S3) and the operator-approved fixes from it. One
required upgrade step: **`python -m chimera migrate-tasks`** once on any
checkout with pre-v7 task records (nine legacy `TaskSpec` fields are gone and
are stripped, not tolerated). Also: re-run `chimera install-agents`
(role files unchanged in shape, but the hook and routing changed underneath).

### Added

- **G2 operator token** (`gates.verify_operator`, `chimera g2-token init|check`,
  `.g2-operator.sha256`): `approve`/`reject` refuse without the operator's
  secret, before any write; `chimera status` reports `g2_enforcement`.
- **Warn-only push-scope advisor** (`.claude/hooks/push-scope-advisor.py`): a
  `git push` carrying non-framework top-level paths, data files under
  `tasks/`, or files over 500 KB gets a warning and a structural log line;
  it never blocks.
- **ADR-011 build mode (Proposed)** with the saved workflow
  `/chimera-build-mode` and the prove-first runbook.
- **Hygiene bundle**: `requirements.lock` with hashes (CI installs from it),
  a CI `lint` job (ruff check, ruff format, mypy), SHA-pinned Actions,
  `dependabot.yml`, pre-commit (ruff, mypy, gitleaks, boundary-token scan,
  large-file guard), PR template, CODEOWNERS, CONTRIBUTING, SECURITY.
- **`scripts/gates.py`**: one local runner for the four CI gates (ruff check,
  ruff format, mypy, pytest) that checks each tool's version against the
  `dev` extra's pin BEFORE running it and refuses on a mismatch — a newer
  container ruff passing locally while the pinned one failed in CI is the
  lesson it exists for. `--no-tests` runs the three static gates only.
- **CI `security` job** on every push and PR: `pip-audit` over the
  hash-locked closure (`require-hashes`) and `gitleaks` with
  `.gitleaks.toml` over the pushed commits. Read-only permissions.
- **`.github/workflows/claude-review.yml`**: every non-draft PR gets the
  `code-review` plugin's inline comments plus one `/security-review`
  summary, authenticated by the subscription OAuth token
  (`CLAUDE_CODE_OAUTH_TOKEN`) — no API key. The steps self-skip and the job
  passes while the secret is absent.
- **Memory redesign** (Q10): attempt-suffixed L2 run summaries
  (`arc_memory.append_run_summary`, `run-summary-<n>`) so a retry never
  overwrites the prior lesson; run ABSTRACTS (ask, shape, artifact BLUF, what
  the panel refuted en route, tallies via `graph.run_abstract`) instead of
  counters; the priors seed reads L2 (FTS then recency) plus L1
  feedback/project/pattern rows, every read separately guarded, block ≤ 1,500
  chars; `GraphArcState.verify_notes`; `prune --older-than-days N [--type T]
  [--dry-run]`; read commands refuse an absent DB instead of creating one;
  `chimera archive` reports `memory_captured`.
- `tests/test_push_scope_hook.py`, `tests/test_g2_token.py`,
  `tests/test_router_hook.py`, retired-record archive drills.

### Changed

- **Dispatcher is a cloud Routine** (`chimera tick (dispatcher)`, every 4 h,
  fresh session per fire); "no cron lane" is retired from the docs.
- **Router hook is a counter**: excerpt-free by construction, imports nothing
  from the package, always allows; `routing.py` keeps only
  `validate_selection` + `registry_from_agents` (327 → 55 lines).
- `ready → archived` is legal for retired-arc records only
  (`queue.CONDITIONAL_TRANSITIONS`), so a stranded pre-v7 record clears with
  `chimera archive`.
- `src/chimera/prompts/references/` → `decisions/references/` (read by no
  code, never shipped in the wheel).
- CLAUDE.md slimmed to numbers and pointers (180 → ~100 lines); `.env.example`,
  README, TICK_PROTOCOL corrected (answer syntax, ceilings, DB default,
  `CHIMERA_ROOT` scope, `--file` steer).
- Committed router telemetry reduced to structural fields; the rotation
  script and checkpoint step are deleted.

### Removed

- `role_memory.py` and its CLI plumbing (zero callers), `gitio.push_remote_host`,
  the repo-root `agents/` stub, `scripts/rotate_router_audit.py`, the nine
  legacy `TaskSpec` fields with `BrandContext`/`AestheticRegister`, the
  tracked personal task record, PR #38 (closed, ADR number collision).

### Fixed

- The G1 Issue comment named a nonexistent `--q1` flag (P1-8).
- `priors_block` OR-joined raw tokens, so an ask containing NOT/AND/NEAR
  raised an FTS5 syntax error and the blanket except returned an empty seed
  (P1-7); tokens are quoted and the fallback sits outside the try.
- PASS and FAIL L2 summaries shared one dedup key (P1-6).
- The PASS summary write and the archive capture were not fail-open (P2-12).
- A retired-arc `ready` record could not be cleared by any verb (P1-11).
- `.env.example` documented `CHIMERA_ROOT` and the DB default wrongly (P1-9);
  TICK_PROTOCOL cited retired 3600 s ceilings (P2-10).
- `g2-token init` could mint a token beginning with `-` (one in 64 draws);
  `approve --token <that>` then exited 2 from argparse. The generator redraws
  until the first character is alphanumeric; existing tokens are unaffected.
- Caught during the new `security` job's bring-up: gitleaks lists a PR's
  commits through the API on `pull_request` events, so the job needs
  `pull-requests: read`. Without it every PR run died with "Resource not
  accessible by integration"; the job shipped with the permission.

Suite: 344 passed; `ruff check`, `ruff format --check`, `mypy src` clean.

## [7.1.1] — 2026-08-28

Adversarial hardening of the v7.1 surface. A 15-agent fan-out over five
attack lenses (fence grants, test adequacy, maker≠checker, admission,
repair lap) plus a hand-built collapse matrix found six defects the v7.1
clearance round left open — two of them deferred there by name. Every fix
ships with a drill, and every drill was mutation-tested (revert the fix,
watch it go red) before landing.

### Fixed

- **A `planner` node inside a plan collapsed maker≠checker.** `planner` runs
  the maker tier but sat outside the producer set, so a checker reading one
  derived no producer model, fell through to the judge tier, and landed on
  the maker model — a silent same-model pair. Two independent closures:
  `_producer_models` is now a DENY-list (every non-checker role counts, so a
  role added to `GraphRole` later cannot fall out of the derivation), and a
  planner node is refused outright at admission — the planner emits plans, it
  is never a node in one.
- **A checker could be dispatched on the model its producer actually ran on.**
  Models resolve at CALL time and nothing recorded what ran, so swapping the
  tiers between the maker's tick and the checker's tick derived a checker
  model that was distinct from the CURRENT maker tier and identical to the
  model the maker really used. Arc state now records the dispatched model per
  node (`node_models`) and checkers derive against that record.
- **The operator's shape pin could be unpinned by editing arc state.**
  `load()` re-checked the persisted plan against a `shape` read from the same
  hand-editable file as the plan; nulling it made `_check_shape` a no-op and
  a widened plan reloaded clean. The pin is now read from the G1 task record,
  and a disagreement refuses loudly.
- **A repair lap left sibling verdicts standing against deleted content.**
  Re-running a maker only invalidated the executor that triggered the lap; a
  sibling that had already landed PROCEED kept an approval of maker content
  that no longer existed. A lap now invalidates the maker's whole read
  closure and re-runs it in phase order.
- **Only the first executor in a phase could ever earn a repair lap.** A
  sibling PAUSE arriving mid-lap was logged and dropped (v7.1.0's R-3 made it
  loud, which is not the same as making it work). It is now deferred and gets
  its own lap when the queue drains.
- **`AgentDef` had no compound-grant rule** (v7.1 clearance follow-on): the
  write+shell / write+network guarantee lived only on `RoleFence`, while the
  grant that actually installs is the one `internal_roles()` widens with
  operator MCP tools and renders into the `.md` files. The rule is now a
  shared `roles.check_grant()` enforced on both.
- **An impossible model posture failed mid-run instead of at admission**
  (v7.1 clearance, R-2): `_check_tiers` counted distinct producer models but
  never ran the derivation, so MAKER == CRITIC admitted and raised at the
  checker's phase, after the maker's calls were spent. Admission now runs the
  real derivation and refuses into the re-plan lap.

### Changed

- `EXEC_REPAIR_SKIPPED` is now `EXEC_REPAIR_DEFERRED`, and the v7.1.0 R-3
  drill is superseded by one that asserts the deferred lap actually runs.

- **A read-less judge on the critic tier was invisible** (v7.1 clearance,
  R-4). Setting `CHIMERA_JUDGE_MODEL` to the critic value has a judge
  adjudicate critics on the critics' own model — the diamond shape the
  planner prompt recommends. By operator ruling this WARNS rather than
  blocks: `graph.judge_tier_warning()` emits
  `JUDGE_TIER_SHARES_CRITIC_MODEL` at admission and the flag rides the
  digest. The covering test asserted `node_model(judge) ==
  resolve_models().judge` — true for any lever value, so it could never
  fail; it now asserts the judge tier is actually distinct from the critics
  it merges.

Suite: 302 passed.

## [7.1.0] — 2026-08-28

The operator-approved frontier-fit batch — the adversarial panel's deferred
roadmap, executed the same day (items approved verbatim: model resolution,
judge tier, Exa for researcher/critic, the repair lap, the optimization
batch).

### Added

- **Call-time model resolution** (`agents.resolve_models()`): model levers
  are read on every call, mirroring the graph levers — an env changed after
  import now reaches the maker≠checker guard; `derive_research_critic_model`
  draws ONLY from configured tiers and refuses when none differs (the
  hardcoded-alias fallback is gone — audit OP-13).
- **`CHIMERA_JUDGE_MODEL`** — fourth model lever: the planner call and
  read-less judge (fan-in) nodes ride it; defaults to the maker value, so
  raising the shape/fan-in tier can never collapse maker≠checker.
- **MCP network grants** (`CHIMERA_RESEARCH_MCP_TOOLS`): operator-granted
  `mcp__<server>__<tool>` names (e.g. Exa) extend the researcher/critic
  fences at call time and render into the installed role files. `mcp__*`
  classifies as NETWORK in `roles.py`, so write+mcp stays unconstructible;
  strict list-wide parse — any malformed entry reads the whole lever unset.
- **Executor→maker repair lap**: an executor node landing PAUSE re-runs the
  maker node(s) it read (carrying the executor's findings), then itself —
  sequential via `repair_queue`, bounded per executor by
  `CHIMERA_GRAPH_REPAIR_LAPS`; exhaustion lets the PAUSE stand and ride the
  digest (flags never block). make→check→revise is now a loop in CODE; the
  DAG stays acyclic. Admission estimates carry the repair allowance.

### Changed / Fixed

- Structural re-admission at load (`graph.check_admitted`): a hand-widened
  persisted plan fails at load instead of resuming with calls admission never
  saw (audit OP-8); lever clamps and the tier check deliberately stay
  plan-time/call-time.
- `plan`/`wrap` ride the 1800s call ceiling (audit OP-15) — a container
  reclaim mid-plan no longer halts the task on the 300s hair trigger; expiry
  there remains the halting class.
- The L2 priors seed reaches WORKER nodes, not only the planner (the
  parameter was dead); checker prompts still never carry it.
- The digest flags PAUSE recommendations alongside low confidence.
- Dead v6 drag deleted: `runner.is_timed_out`, the digest's `result.json`
  reader, `_common.RESULT_FILE`; `_common.py` docstrings reconciled to the
  one-arc reality. (`routing.decide_domain`/`catalogue_registries` STAY —
  the router-enforcer hook that writes the committed `router-audit-*.jsonl`
  telemetry is a live caller; the panel's "no caller" finding missed it.)
- Suite: 260 → 274 passed; suite made hermetic against a developer's real
  `CHIMERA_*` env levers (autouse fixture).

## [7.0.0] — 2026-08-28

### Fixed — the adversarial verification gate (pre-release panel, same day)

An Opus-5 adversarial auditor and a Sonnet-5 empirical validator reviewed the
consolidation independently, then cross-examined each other's findings (full
trail: `decisions/audits/2026-08-28-v7-adversarial-verification.jsonl`). All
14 architecture invariants held under counterexample attack; the panel's
merge gate landed as:

- **Checker output contract (OP-1).** `graph.checker_brief` now states the
  GraphNodeOutput contract (with the checker's own node id); a checker
  answering the old prompt as written returned prose, failed the schema gate,
  and degraded to `--null` on first real use.
- **Write serialization (OP-2).** `arc submit` / `arc next` take the
  queue-state writer lock (blocking form of the tick lock). Reproduced twice:
  concurrent submits on one phase lost a node output from COMMITTED state
  while the losing submit still exited 0.
- **Poisoned-task isolation (OP-3).** An arc that raises while tick resumes
  it (e.g. a model env tripping the maker≠checker guard) is parked failed
  with the real error — F7 extended; one bad task no longer wedges the queue.
- **The 250-call ceiling is real again (OP-4).** Every submission (payload
  and null paths) routes through `runner.issue_call`; the ceiling had been
  dead code since the v6 arcs' call sites were deleted. Exceeding it halts
  the run; `by_label` accounting is live again.
- **`install-agents` frontmatter (SN-1).** `tools:` renders as the documented
  bare comma list; the harness rejects the YAML flow-list form at subagent
  launch (fails closed), which would have made every installed role file
  unusable. The old test asserted the renderer's own format back at it —
  rewritten against the documented contract.
- **Hard caps on the type (OP-6).** `GraphLevers` fields carry `le=` hard
  maxima, so code-built levers can't exceed what the env parser grants.
- **Unknown shape refuses (OP-7).** A garbled G1 shape pick refuses at
  admission (the old path silently UNPINNED the pick — the loosening
  direction); `GraphArcState.shape` is typed to the Literal so a hand-edited
  state fails at load into the tick's corrupt-state park.
- **Verify truncation is marked (OP-12).** The terminal panel's payload cap
  rises to 48k chars and truncation is announced in the prompt, never silent.
- **Version identity (SN-2/SN-3).** `__version__`, pyproject, and the
  package docstring agree (pinned by test); the live critic prompt no longer
  introduces itself as "chimera v6".
- New tests for all of the above plus the retired-`running` park drill
  (SN-6): 246 → 260 passed.

### Changed — MAJOR: one arc, six roles, operator-picked shape (ADR-010)

The operator's consolidation overrule, executed the same day 6.9.0 landed the
graph runtime additively. The graph is now chimera's ONLY runtime.

- **One door.** `chimera new "<ask>"` — no `--arc`, no per-arc flags.
  `LAUNCH_ARCS == ("graph",)`. The planner composes the retired arcs' shapes
  as data.
- **The eight fixed pipelines deleted** (research, design, proposal, build,
  n8n, comms, reflect, gemini): modules, payload schemas, per-arc intake
  gates, CLI flags, prompt persona templates, and their test files. Records
  written before the consolidation still load (`models.RETIRED_ARCS`) for
  status/history/archive; dispatching one raises with a re-open pointer;
  `next_runnable()` skips retired ready tasks and tick surfaces them as
  `retired_ready` (F7 applied to retirement); a retired `running` record is
  parked failed at tick.
- **Personas dead.** `agents/catalogue/` (57 files) and the ~50-entry roster
  + 10-slot design team deleted. `agents.ROSTER` is exactly the six fenced
  roles, tool grants taken verbatim from `roles.FENCES` (lockstep-tested);
  the audited 17 write+shell catalogue grants are gone with it.
  `install-agents` writes six role files; `which-agent`, `brands`, `reflect`
  commands removed; routing slims to its decision core
  (`catalogue_registries()` degrades to empty by design — tested).
- **Operator shape pick** (rev-2 design: every automatic shape router failed
  adversarial validation): `chimera new --shape straight|diamond|pipeline`
  records the pick on the TaskSpec, the planner is told it is pinned, and
  `graph.admit()` refuses a non-conforming plan into the re-plan lap. No
  pick = the planner proposes within the levers (interim until the
  deterministic recommender lands).
- Distilled craft kept as data: `src/chimera/prompts/references/` (rubrics,
  vocabularies, n8n/a2a checklists) survive for planner briefs to cite.
- Honesty fix: `pending_calls` during verify no longer re-lists critics whose
  opinions already landed; graph submits route through the schema gate so a
  malformed payload gets the framed N8 error, not a raw traceback.
- Suite: 246 passed (full parity over the one arc + retired-arc drills +
  the graph e2e CLI lifecycle).

## [6.9.0] — 2026-08-28

### Added — graph arc: the frontier rebuild lands as the ninth arc (ADR-009)

- **`arcs/graph.py`** — the planner-emitted-DAG runtime. A planner node emits
  a `GraphPlan` (phases of role-fenced nodes; a node reads strictly earlier
  phases only, so cycles are unrepresentable); `graph.admit()` clamps it
  BEFORE any work node runs; nodes fan out in parallel within a phase with a
  barrier between phases; wrap + the unchanged 3-critic REFUTE panel + G2
  close the run. Registered in `tests/arc_drivers.py::ARCS` — all five parity
  suites green unmodified, plus 63 dedicated tests.
- **`graph.py`** — admission (width/phase/budget clamps with lever-naming
  refusals, read resolution, checker-must-read, the fan-in guard: 2+
  same-role producers in a phase must all be read by one later node),
  `estimated_calls()` (the budget model), `node_model()` (checker models
  derive distinct-by-construction from the producer they read; mixed-tier
  producer reads are refused), `checker_brief()` (the input-set invariant:
  a checker sees exactly {ask, rubric, read artifacts}).
- **`roles.py`** — six roles over four distinct tool grants; capability
  DERIVED from the grant; write+shell and write+network unconstructible;
  roster mapping validated to fit inside each fence (executor deliberately
  unmapped — no roster member holds a shell).
- **`levers.py`** — autonomy levers, operator-added, never engine-assumed:
  `CHIMERA_GRAPH_WIDTH` (3, cap 8), `CHIMERA_GRAPH_PHASES` (5, cap 10),
  `CHIMERA_GRAPH_CALL_BUDGET` (40, cap 250 = the runner ceiling),
  `CHIMERA_GRAPH_REPAIR_LAPS` (1, cap 3). Strict parse — a typo reads as
  unset; env read in this module only (tested).
- Bounded loops embedded in the DAG, in code: admission refusal feeds ONE
  re-plan lap (the refusal text, which names the lever, goes back to the
  planner); verify refutation runs the shared critique→rewrite lap, bounded
  by the repair-laps lever. `finalize_verify_with_repair` gains a
  `max_repairs` passthrough (the six wrap+lite-verify arcs unchanged).
- Plan of record: `decisions/plans/2026-08-28-frontier-graph-rebuild.md`
  (phases B/C: catalogue collapse to role files, calibration from real runs).

## [6.8.0] — 2026-08-01

### Added — arc-scoped agent install (lean default)

- **install-agents scoping:** `python -m chimera install-agents` now installs a
  **lean default** (catalogue + internal roles, excluding the 9 design-arc
  roles and the design category — ~250KB of rarely-dispatched agent prose no
  longer syncs to `~/.claude/agents/` unconditionally). New CLI surface:
  `--all` (previous full-sync behavior), `--arc <name>` (repeatable; installs
  the categories mapped to an arc — e.g. `--arc design` restores the design
  roles), `--category <name>` (repeatable). The library-level
  `install(..., categories=None)` default preserves the old full-sync
  behavior for programmatic callers; the lean default is CLI-only.
  Arc→category map lives in `agents/catalogue/manifest.py`
  (`lean_categories()`); covered by `tests/test_install_scoping.py`.
- **hygiene:** `.ruff_cache/` added to `.gitignore`.

### Changed — repo hygiene pass (slim-and-tidy)

Docs/dead-weight cleanup. All removed files were
copied to a local quarantine directory (outside the repo) before deletion —
recoverable via git history regardless.

  files to the load-bearing `tokens/tokens.css` + `tokens/tokens.json` that
  `tests/test_brands.py` and the design arc actually depend on.
- **tasks:** three completed/archived task records
  (`20260702-m01-depth`, `20260702-reflect-232845`,
  `20260717-gemini-enterprise-contract-redliner`) untracked from `tasks/`.
- **audits:** stale `decisions/audits/router-audit-2026-07-26.jsonl`
  dropped; the `router-audit-*.jsonl` prefix convention (reflect's glob)
  is otherwise untouched.
- **skills:** `portfolio` skill moved to user scope (`~/.claude/skills/`);
  no framework arc depended on it living in-repo.
- **plans:** six superseded planning docs (three 2026-07-02 audit-cycle
  docs, `v6-plan.md`, `loop-engineering-gaps.md`,
  `2026-07-01-agent-landscape.md`) replaced by a single ~5.4KB distillation,
  `decisions/plans/2026-07-02-audit-cycle-summary.md`
  (what the audit found, what the panel decided, what shipped — full
  line-level detail stays in git history). `.claude/skills/mirror-audit/SKILL.md`'s
  example updated to no longer name a file that no longer exists.
- **docs:** `README.md` rewritten to stop duplicating `CLAUDE.md` (model-alias
  mechanics, arc-loop step detail) and to reflect the current top-level
  layout; `BACKLOG.md` pruned of entries that are shipped, cancelled, or
  otherwise resolved (each pruned entry's disposition is recorded in the
  commit history, not silently dropped); `when_to_use.tsv` headers in
  `agents/catalogue/` and `src/chimera/` disambiguated (catalogue vs
  in-arc roster scope); `scripts/chimera_memory.py` docstring marked
  DO-NOT-DELETE (4 live invocation sites).

## [6.7.0] — 2026-07-27

### Added — reflect arc v2 (GEPA-shaped reflection + stdlib injection defense)

The reflect arc's analysis is reframed from free-text "reflect on the run" to the
**GEPA pattern** (layer d): it learns from graded examples (example + score + the
natural-language reason for the grade), enforces anchor-or-drop, and requires a
falsifiable acceptance test per proposal. This replaces the layer-b self-editing
shape that repeatedly failed validation. Decision of record: **ADR-008**.

- **GEPA reflection prompts** (`arcs/reflect.py` `_analyze_prompt`/`_wrap_prompt`).
  Reflect stays propose-only, never self-applies; every proposal rides the 3-critic
  REFUTE panel + human G2. Gate-loosening suggestions are recorded verbatim under
  `uncertain`, never proposed.
- **New stdlib injection defense** (`src/chimera/injection.py`): `scan` → `contain`
  → propose-only/REFUTE/G2 over the UNTRUSTED past-run corpus. Placed outside
  `arcs/` so the no-write grep stays clean. Evidence intake is **structurally
  fail-open** — a scanner error can never block an arc start. Not a reintroduced
  sensitivity layer (ADR-003): no per-task label, no org strings, stdlib-only.
- **Judge-panel per-kind expiry ceiling** (reflect M11, `arcs/research.py` +
  `runner.py`) so a slow kind cannot starve the panel.
- Backed by 19 reflect tests + 8 injection tests; the arc remains registered in the
  cross-arc parity suite.
- **Not claimed:** that applying reflect's proposals *compounds* future runs. That
  needs a held-out acceptance harness and is deferred, gated on corpus volume
  (~6-8 more graded runs). Shipped as engineering + hardening, per Behavioral Rule 6.

## [6.6.0] — 2026-07-16

### Added — gemini arc (8th arc: the Gemini-platform member of the platform-builder family)

chimera gains a record-only builder for Gemini Enterprise deliverables,
realizing chimera-as-GE-meta-builder. It emits more than ADK agents: A2A
topologies, MCP connectors, data stores, RAG corpora, grounding, and
console-handoff Designer runbooks.

- **New arc `gemini`** (`arcs/gemini.py`): a `GeminiDeliverable` manifest
  (declared at G1 via `--manifest`) → scope → topology → prompts (A2A
  prompt-tuning: a 7-axis REFUTE panel iterating until `overall_min >= 8`, with
  plateau detection) → emit (per-artifact specialist fan-out via
  `routing.gemini_select_specialist`, ≤5/phase; `adk_agent` drives the real ADK
  CLI via `adk-cli-grounding`) → mechanical OPA-posture gate (folded into
  emit→wrap) → wrap → verify. Reuses the `arcs/_common` spine and is
  parametrized into the cross-arc parity suites.
- **Record-only, never-deploy.** The arc orchestrates the driving session's
  specialists against a skeleton fork and records `emitted_paths` only; it never
  applies Terraform, calls GCP, deploys, or registers live.
  `GeminiFrontmatter.deployed` is pinned `Literal[False]`. Shipping is the
  separate, gitignored, post-G2 `deploy` skill.
- **`designer_agent` console handoff.** The one non-automatable, console-only
  surface degrades to a console-handoff runbook rather than emitting code.
- **Brand/corp-agnostic (Security Rule #1).** No cluster/project/policy/ticket
  specifics in tracked code; work-specific planning content lives in the
  gitignored `overlay/` zone.
- **Freeze re-proven (Behavioral Rule #5).** The M7 arc-surface freeze required
  invariant parity across all arcs; `gemini` clears it by construction —
  registered into `tests/arc_drivers.py::ARCS` and green across the parity
  suites. Full harness 653 green.
- **Roster + intake.** `adk-builder`, `gemini-architect`, `agent-architect`, and
  `prompt-engineer` added to `agents.py` (all `external=True`); `TaskSpec.manifest`
  and a `--manifest` CLI flag added for the gemini intake.

### Superseded

- The interim `gemini-build` arc (a thinner, ask-driven scaffolder merged earlier
  on 2026-07-16) is superseded by this `gemini` arc, which folds in its
  `deployed: Literal[False]` governance graft — a best-of-both reconciliation of
  two parallel efforts.


## [6.5.0] — 2026-07-01

### Added — comms voice library + hardening

The comms arc's voice system is expanded and hardened.

- **Voices: 6 → 11.** Added `exec-impact` (full ROI/risk/recommendation
  business case), `vision` (mission/values writing), `advanced-explainer`
  (expert-but-new-to-this; the inverse of beginner `training`),
  `strategic-advisor` (hard-truths analysis with frameworks), and
  `systems-architect` (technical architecture writing for operators). Added a
  `technical` audience. `LAUNCH_ARCS` corrected to include `n8n`.
- **Single source of truth.** All voice guidance now lives in
  `chimera/voices.py` (`VOICE_GUIDANCE`); the duplicated inline dict in
  `comms.py` is gone. A parity test asserts every `Voice` literal has guidance
  and a section in `comms-voice-library.md`, so the three can't drift.
- **Voice-critic calibration (resolves handoff blocker #2).**
  `VOICE_CRITIC_CALIBRATION` — worked good-fit / over-explained / under-explained
  / tone-mismatch examples — is injected into the voice-gate critic prompt so
  audience-fit is judged against anchors, not vibes.
- **Personal voice-pack overlay.** A gitignored `voice-packs/<voice>.md` is
  appended to a voice's guidance at draft time via `load_voice_overlay`
  (override dir with `CHIMERA_VOICE_PACKS_DIR`). Framework ships none; a clean
  clone runs on the generic guidance. Personal / brand-specific voice content
  lives only here and never enters a commit (Security Rule #1).
- **Roster guard.** A test asserts the five comms roles (`exec-advisor`,
  `architect`, `writer`, `synthesizer`, `critic`) still resolve to real
  specialists, turning a future silent fail-open degrade into a loud failure.

447 tests green.

## [6.4.0] — 2026-07-01

### Added — n8n arc

`chimera new "<ask>" --arc n8n` builds, validates, and tests an n8n workflow
against the live n8n MCP server, then STOPS before publish — the arc never
publishes or activates a workflow (a human activates after sign-off). State
machine: `scope → design → pre_gate → build → validate → structure → test →
wrap → verify`. Adds the `n8n-architect` maker role and curated references
(`n8n-core.md`, `n8n-validation-checklist.md`); node types, parameters, and
typeVersions are confirmed via the live MCP, never from memory. (Landed
alongside domain-aware router-enforcer routing.)

### Added — comms arc

The comms arc lands on first real need (trainings-going-public +
presentation of automations). `chimera new "<ask>" --arc comms --upstream
<path> --audience <a> --voice <v> --output-kind <k>` turns any upstream
artifact (research / design / proposal / build) into an audience-shaped
written output (`{output_kind}.md`).

- **State machine** (`src/chimera/arcs/comms.py`, mirrors `proposal.py`):
  `brief` (exec-advisor) → `structure` (architect) → `draft` (writer) →
  `voice_gate` (3-critic REFUTE) → `wrap` (synthesizer) → `verify` →
  `done|halted`.
- **6-voice library**: `internal-casual`, `stakeholder-update`,
  `exec-summary`, `external-announcement`, `training`, `deck-narrative`
  (the last two added for the public-training + automation-presentation use
  case). **5 audiences** (adds `learner`). **`output_kind`** enum
  (`digest|brief|training|deck-script|announcement`) encodes the filename.
- **Soft voice_gate**: 3 Sonnet critics REFUTE on tone mismatch, audience
  misfit, unsourced claim, or >30% word-budget overrun. Non-survival stamps
  the artifact `partial` and rides the digest; it never halts. The hard gate
  remains the final lite verify before `done`.
- **One chain-rule exception**: comms may read any of the four upstream
  artifacts, declared at intake (`gates._require_comms_upstream`).
  `external-announcement` voice requires `--external-ok`.
- New schemas in `models.py` (`CommsBrief`, `DocStructure`, `CommsDraft`,
  `CommsFrontmatter`, `CommsArtifact`, …) + `Voice`/`Audience`/`OutputKind`
  literals; `comms` added to `LAUNCH_ARCS`. Voice reference at
  `src/chimera/prompts/references/comms-voice-library.md`. 64 new tests.

## [6.2.0] — 2026-06-23

### Upgrade note

Closes the design arc's last specialist gap. After pulling, **re-run
`python -m chimera install-agents`** so the 7 design-team subagents
(`deck-designer-v2`, `infographic-designer`, `data-viz-specialist`,
`marketing-asset-designer`, `copywriter`, `design-critic`,
`consistency-critic`) are provisioned into `~/.claude/agents/`. They are
written **self-contained** — the rubric, vocabulary, and iteration-runbook
references are inlined into each agent `.md` so a driving session does not
depend on having chimera checked out on disk. Without the re-run, a design
task's iterate stage names a `subagent_type` the session cannot resolve and
falls back to general-purpose.

### Added
- **Design arc iterate-stage specialist wiring.** `arcs/design.py`'s
  `_iterate_calls` now names the builder's `DESIGN_TEAM` role (e.g.
  `deck-designer-v2`) on the builder `AgentCall` and `design-critic` on each of
  the 3 critic calls, validated deterministically against a **design-only
  registry** via `routing.design_validate_selection`. Previously these were the
  only arc calls still emitting `subagent_type=None`.
- **`routing.design_registry_from_agents()` + `design_validate_selection()`** —
  a DESIGN_TEAM-scoped routing surface kept deliberately separate from `ROSTER`
  (merging would expose design makers, whose allowlist == `ALL_CAPS`, to the
  build-arc selector and the fail-open enforcer hook).
- **`install-agents` now exports the design roles.** New `_install_design_roles`
  in `agents/install.py` + `agents.render_design_role_md` /
  `design_internal_roles` / `load_design_references`. Each design `.md` is
  rendered with a neutral `BrandContext()` (per-task brand is still injected at
  the call site) and has its references inlined. Summary string now reports
  `N catalogue + N internal + N design roles written`.

### Notes
- Maker≠checker preserved: design makers run `MAKER_MODEL` (opus), `design-critic`
  runs `CRITIC_MODEL` (sonnet); the wiring only adds routing context.
- Selection degrades to `subagent_type=None` (never halts the arc) if a role
  fails validation — null-degrade machinery handles the rest.
- 283 tests green (272 + 11 new design routing/wiring/export tests).

## [6.1.0] — 2026-06-23

### Upgrade note

This release makes the arcs name real specialist subagents. After pulling,
**re-run `python -m chimera install-agents`** so the new internal-role
subagents (`scoper`, `investigator`, `critic`, `judge`, `synthesizer`,
`goal-reviewer`, `architect`, `exec-advisor`, …) are provisioned into
`~/.claude/agents/`. Without this step a driving session cannot resolve the
named `subagent_type`s and will surface a provisioning gap.

### Added
- **Specialist wiring on every arc.** `research`, `proposal`, `design`, and
  `reflect` now set `subagent_type` on their `AgentCall`s (previously only
  `build` did), so a session invokes the real pipeline specialist instead of
  general-purpose. Selection is validated via `routing.validate_selection`,
  following the existing build-arc pattern; maker/critic models are unchanged
  (maker = opus, critic = sonnet).
- **Internal-role export in `install-agents`.** The command now also writes
  each `external=False` ROSTER role (with a `system_prompt`) as a
  `~/.claude/agents/<role>.md` subagent, so the names the arcs use actually
  resolve in a session. Catalogue and `.local/` agents take precedence on a
  name clash.
- **`tests/test_push_boundary.py`** — a tracked regression test asserting that
  work-zone paths (`overlay/`, `projects/`, `wip/`, …) are never git-tracked
  (Security Rule #1, enforced in CI rather than only by an off-machine hook).
- **Dev tooling**: pinned `ruff==0.9.6` + `mypy==1.15.0` with lenient configs.

### Fixed
- **`verify_opinions` idempotency bug.** The `proposal` and `design` arcs
  accumulated terminal verify-panel opinions in a list (append), so a
  duplicate critic submit grew the count and could pass the `>= 3` panel check
  with duplicated data. They now key opinions by label in a dict, matching the
  `build`/`reflect` arcs — duplicate submits overwrite, not double-count.
- **`g1_answer` audit trail.** Recording an answer reused the intake writer and
  re-stamped the "G1 questions posted" commit message; it now writes the file
  directly with an honest "G1 answers recorded" message (on-disk format
  unchanged).
- **`read_questions` crash.** A malformed `questions.yaml` raised an uncaught
  `JSONDecodeError`; it now raises a descriptive `QueueError`.

### Security / hardening
- **Commit-author sanitization.** The worker identity (env / git config /
  hostname) is stripped of control characters and length-capped before it
  reaches a commit message, closing a git-trailer-injection vector.
- **Narrowed broad excepts** in the `reflect` (memory search) and `design`
  (brand-context read) arcs so they degrade only on the expected DB/IO errors
  instead of swallowing everything.

### Changed
- **Internal dedup (no behavior change).** `_Strict`, the arc state save/load
  pair, the verify-result reader, and the shared file-name constants moved into
  `src/chimera/arcs/_common.py`; the five arcs import them instead of
  re-declaring (~74 LOC removed from the arc modules).

[6.1.0]: https://github.com/onepromptman/chimera/releases/tag/v6.1.0
