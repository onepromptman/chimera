# chimera v7

Git-durable background task engine driven by **Claude Code cloud sessions on
a subscription** — no API keys, no CI workers; the standing dispatcher is a
cloud Routine running `chimera tick`. Boris methodology:
loops > prompts, externalized memory, adversarial verification, maker ≠
checker, throughput > latency.

100% self-authored framework code; dependencies pinned to exactly
`pydantic==2.13.4` (+ `pytest==9.0.3` dev). Minimal supply chain by construction,
plus a hard boundary rule: nothing work-related ever enters this repo (see
`CLAUDE.md` Security Rule #1).

## Setup

Two ways in. Most days you only need the first.

| You want | Install | Needs |
|---|---|---|
| The checks and the panel, in any repo | the six role agents + two skills from `plugins/chimera/` | Claude Code |
| The queue engine (`chimera new` / `tick`) | this repo + a Python 3.11+ venv | git, Python 3.11+ |

CI runs the suite on Ubuntu (Python 3.11 and 3.12). macOS on Apple Silicon was
set up and run green from these steps. `queue.py` carries a native-Windows lock
fallback, but no CI job covers native Windows.

### 1. Prerequisites

macOS:

```bash
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install git gh uv
```

Homebrew prints two `eval` lines under "Next steps" — run them so `brew` is on
your `PATH`. macOS ships Python 3.9, which is too old; `uv` fetches 3.11 below.

Linux / WSL: install `git` with your package manager and `uv` from
<https://docs.astral.sh/uv/getting-started/installation/>. Under WSL keep the
checkout on the Linux filesystem: `chmod 0600` is a no-op on `/mnt/c`.

### 2. Clone and build the venv

```bash
gh repo clone onepromptman/chimera ~/code/chimera
cd ~/code/chimera
uv venv --python 3.11             # downloads 3.11 if the machine has none
uv pip sync requirements.lock     # the exact hash-locked closure CI installs
uv pip install pip                # scripts/gates.py runs `pip check`
uv pip install --no-deps -e .
.venv/bin/python scripts/gates.py # must end with: gates: ALL GREEN
```

Order matters: `uv pip sync` removes anything not in the lock, so install `pip`
and the editable package after it.

### 3. Install the roles and skills for every repo

```bash
PYTHONPATH=src .venv/bin/python -m chimera install-agents   # six role files -> ~/.claude/agents/
mkdir -p ~/.claude/skills && cp -R plugins/chimera/skills/. ~/.claude/skills/
```

`PYTHONPATH=src` renders the roles from this checkout's code; see the drift
guard in `tests/test_plugin_package.py` for why that matters in a worktree.

Once the plugin is on the public mirror's default branch,
`/plugin marketplace add onepromptman/chimera` then `/plugin install chimera`
replaces both commands. Use one route or the other, not both: two agents with
the same role name is exactly what the skills refuse to run on.

### 4. Check it

```bash
CHIMERA_DB_PATH="$(mktemp -d)/memory.db" .venv/bin/python -m chimera init --check
```

The preflight writes nothing. Then open a **new** Claude Code session with this
folder as the project: the hooks in `.claude/settings.json` resolve
`$CLAUDE_PROJECT_DIR` when the session starts, so a session that moved here
from somewhere else cannot find them.

## Using it day to day

| Skill | Say | What runs | Cost |
|---|---|---|---|
| `chimera-check` | "check this", "gate 0 this" | do the work → `executor` runs it (Gate 0) → `critic` verifies every cited path and URL (Gate 1) | 1–2 agent calls |
| `chimera-graph` | "run this through chimera", "panel this" — also fires on its own for hard-to-undo changes | the full ladder through Gate 2: three independent checkers in fresh context, one executing, one repair lap | about 4 calls |
| `chimera-dag` | "queue this in chimera", "tick chimera" — this repo only | the engine below: `chimera new` → planner DAG → fenced nodes → verify → digest | a run took 51–99 minutes in the 2026-09-11 review |

`chimera-check` and `chimera-graph` ship in the plugin and work in any repo.
`chimera-dag` lives in `.claude/skills/` because it needs this checkout's engine.

## How it works

```
chimera new "<ask>"            G1 — the only intake gate. Ambiguity is
   [--shape straight|           clarified in-session before this runs; the
    diamond|pipeline]           session then picks proceed / straight /
        │                       diamond / pipeline. You pick the shape; the
        ▼                       framework only recommends.
chimera tick                   a cloud session claims the task (claim = commit)
        │                      and drives the graph: every pending agent call
        ▼                      is executed with the session's own Agent tool
graph arc (autonomous)         planner emits a DAG; admission clamps it against
  plan → admit → run           the levers; role-fenced nodes run phase by phase.
  → 2-critic REFUTE            makers = `opus` alias, critics = `sonnet` alias —
        │                      maker ≠ checker enforced in code. Every step
        ▼                      checkpoint-committed + pushed; a dead container
lite verify (2-critic REFUTE)  resumes from the last commit. Fatal-only veto:
        │                      a refuted fatal fails alone, a thin (<2 valid)
        │                      panel fails; the only tier.
        ▼
digest + single Issue thread   async surface: confidence <70 flags, critic
        │                      splits, sign-off request — no interaction needed
        ▼
G2 — review-AFTER              a PASSING panel auto-approves
        │                      (`approved_by: auto:verify-pass`) and goes
        │                      straight to done. A FAILING panel parks at
        │                      awaiting-signoff, and only then do
        │                      `chimera approve`/`reject` apply — operator-only,
        │                      needing the G2 token from `chimera g2-token init`
        │                      (never in a worker). Either way `done` is reached
        │                      only through queue.transition(), which enforces
        │                      the verify gate: a worker cannot self-declare it.
        ▼
archived                       memory captured to SQLite FTS5
```

**One blocking human gate on the happy path: G1 intake.** Since `155286a`, G2
is review-after — a passing panel auto-approves and the task reaches `done`
without parking, so G2 blocks only the runs the panel already doubts. That
asymmetry is deliberate but it is the thing to re-read before trusting an
unattended run: human review now applies exactly where the machine has already
flagged itself.

Everything between is background work that survives disconnects and container
reclaim because **all state is git**: every transition is a commit.

### One arc: `graph`

v7 consolidated the eight fixed pipelines into a single planner-emitted-DAG
runtime (ADR-009, ADR-010). **The DAG is data, the loop is code.** A plan is
phases of role-fenced nodes; reads reference strictly earlier phases, so
cycles are unrepresentable. The bounded loops stay in Python: one re-plan lap
on admission refusal, and verify repair laps.

The eight retired arcs (`research`, `proposal`, `build`, `design`, `n8n`,
`comms`, `reflect`, `gemini`) still **load** as historical records but never
dispatch. The planner composes the retired shapes as data; the distilled
craft survives in `decisions/references/`.

### Six roles, fenced by capability

`agents.ROSTER == roles.FENCES`, lockstep-tested. Capability derives from the
tool grant — **write+shell and write+network are unconstructible**:

| Role | Tools |
|---|---|
| `planner` | Read, Grep, Glob |
| `researcher` | Read, Grep, Glob, WebFetch, WebSearch |
| `maker` | Read, Grep, Glob, Write, Edit |
| `executor` | Read, Grep, Glob, Bash |
| `critic` | Read, Grep, Glob, WebFetch, WebSearch |
| `judge` | Read, Grep, Glob |

Checker nodes see exactly `{ask, rubric, read artifacts}` (`graph.checker_brief`
— the input-set invariant) and derive a model distinct from the producer they
read. Persona prompt files are gone; the roster is the contract.

### Autonomy levers

`graph.admit()` clamps every plan. Default-restrictive, read only in
`levers.py`; a typo reads as unset:

| Lever | Env var | Default | Hard max |
|---|---|---|---|
| Phase width | `CHIMERA_GRAPH_WIDTH` | 3 | 8 |
| Phase count | `CHIMERA_GRAPH_PHASES` | 5 | 10 |
| Repair laps | `CHIMERA_GRAPH_REPAIR_LAPS` | 1 | 3 |

A `--shape` pick is enforced at admission: a non-conforming plan is refused
into the re-plan lap. No pick means the planner proposes within the levers.

## Layout

```
chimera/
├── src/chimera/             # the package — see each module's docstring for
│   │                        #   what it owns, not a doc
│   ├── models.py            # every schema (Pydantic v2, extra=forbid)
│   ├── queue.py             # 6-state git-durable machine; done only via
│   │                        #   verify gate
│   ├── graph.py             # DAG admission, checker_brief, node_model
│   ├── roles.py             # the six capability fences
│   ├── levers.py            # the only reader of the autonomy env vars
│   ├── runner.py            # call ceiling + checkpointing
│   ├── routing.py           # validates a planner's specialist pick
│   ├── arcs/graph.py        # the one live arc (_common.py = the priors seed)
│   ├── memory.py            # SQLite+FTS5 (shim: scripts/chimera_memory.py)
│   ├── arc_memory.py        # L2 arc-memory adapter
│   ├── agents.py            # ROSTER as code: six roles, their prompts + tiers
│   ├── gitio.py             # git plumbing: commit, push-with-backoff
│   ├── gates.py             # G2 approve
│   ├── digest.py / notify.py # async surface (daily rollup + Issue payloads)
│   └── verify/              # schema_gate + lite 2-critic REFUTE
├── plugins/chimera/         # the shippable plugin: six generated role agents
│                            #   + the chimera-check and chimera-graph skills
├── tests/                   # the harness: state machine, arc parity fixtures
│                            #   (tests/arc_drivers.py::ARCS), graph admission,
│                            #   maker≠checker guard, e2e dry run
├── decisions/               # ADRs, plans, audits, and references/ — the
│                            #   retired arcs' distilled craft, read by
│                            #   people, not by code
├── flows/                   # arc contracts (SPEC.md, law — schemas live
│                            #   in models.py)
├── scripts/                 # memory search shim + maintenance scripts
├── .claude/                 # skills (incl. chimera-dag), hooks, settings —
│                            #   session-side, not package runtime
├── .github/workflows/       # ci.yml: pytest (3.11 + 3.12), lint (ruff +
│                            #   mypy), security (pip-audit + gitleaks);
│                            #   claude-review.yml: per-PR code + security review
├── wip/                     # gitignored scratch + checkpoints
├── CHANGELOG.md
├── CLAUDE.md                # operating rules for the cloud session
├── TICK_PROTOCOL.md         # worker bootstrap prompt for cloud sessions
└── pyproject.toml
```

`tasks/` (the queue) and `digest/` (daily rollups) are created on first
`chimera new` / first digest run and are normally absent from a fresh
checkout; a task record that IS tracked is a hygiene finding, not a fixture.

## Quick start: the engine

After [Setup](#setup), from the repo root (or say "tick chimera" and let the
`chimera-dag` skill drive these):

```bash
source .venv/bin/activate
python -m pytest -q                                   # green first
python -m chimera init                                # setup wizard -> writes .env
python -m chimera new "compare X and Y"               # optionally --shape diamond
python -m chimera tick                                # then follow TICK_PROTOCOL.md
```

`chimera init` walks the four things a first run needs decided — identity,
model tiers, memory location, autonomy levers — and ends in preflight.
`chimera init --check` is that preflight alone and **writes nothing**, so it
is safe to run any time; its load-bearing check is maker != checker, the one
misconfiguration that otherwise surfaces only after an arc has already spent
maker calls.

`python -m chimera --help` is canonical for the full CLI surface.

## Releases & updating

chimera follows [Semantic Versioning](https://semver.org/); the current
version is in `pyproject.toml` and every change is logged in
[`CHANGELOG.md`](CHANGELOG.md) — those two are canonical (git tags exist for
some older releases but lag behind; don't version-check against tags).

To check whether you are behind and update:

```bash
grep '^version' pyproject.toml    # the version you have
git pull                          # take the latest framework
uv pip sync requirements.lock && uv pip install pip && uv pip install --no-deps -e .
PYTHONPATH=src .venv/bin/python -m chimera install-agents      # re-provision the six roles
cp -R plugins/chimera/skills/. ~/.claude/skills/               # refresh the two skills
.venv/bin/python scripts/gates.py # confirm green
```

The top entry of `CHANGELOG.md` always names any required upgrade step.
**v7.0.0 is a MAJOR break**: `--arc` and the per-arc flags are gone, and
persona catalogue files are no longer installed.

## Status & backlog

Open work is tracked in the maintainer's private backlog, not in this public copy. Design
history and superseded proposals live under `decisions/`.

## License

MIT — see `LICENSE`.
