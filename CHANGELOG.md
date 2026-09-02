# Changelog

Notable changes to chimera. Format follows [Keep a Changelog](https://keepachangelog.com/);
versioning is [SemVer](https://semver.org/).

- **MAJOR** — incompatible change to the queue state machine, on-disk artifact
  formats, or the tick protocol contract.
- **MINOR** — backward-compatible capability.
- **PATCH** — backward-compatible fix or hardening.

`pyproject.toml` and this file are canonical for the current version.

## [7.1.1] — 2026-09-02

First public release.

### Added

- `chimera init` — a setup wizard covering identity, model tiers, memory
  location, and the autonomy levers. Writes a `.env`, backs up rather than
  replaces an existing one, and runs preflight afterward.
- `chimera init --check` — preflight alone, writes nothing. Reports the
  resolved model tiers, whether maker and critic actually differ, the active
  lever values, the memory DB path, and git reachability.

### The engine

- One arc: **graph**, a planner-emitted DAG. The DAG is data (phases of
  role-fenced nodes; reads reference strictly earlier phases, so cycles are
  unrepresentable) and the loop is code (one re-plan lap on admission
  refusal, verify repair laps, an executor→maker repair lap).
- Six capability-fenced roles. Capability derives from the tool grant, so
  write+shell and write+network cannot be expressed.
- maker ≠ checker enforced in code: a panel whose critics share the maker's
  model is refused rather than run. Checker models derive from the model the
  producer was actually dispatched on, so lever drift between ticks cannot
  collapse the distinction.
- Two blocking human gates (intake, sign-off). Every state transition is a
  git commit, so an interrupted run resumes from the last commit.
- Autonomy levers are default-restrictive with hard caps that cannot be raised
  from the environment; a malformed value reads as unset.
- SQLite FTS5 memory outside the repo by default. Reads fail open.

312 tests. No network calls, no credentials, no external services.
