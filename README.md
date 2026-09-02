# chimera

A git-durable background task engine for Claude Code sessions.

You hand it an ask. It plans a DAG of role-fenced agent nodes, runs them phase
by phase, has independent critics try to refute the result, and comes back for
your sign-off. Every state transition is a git commit, so a dropped connection
or a reclaimed container resumes from the last commit instead of starting over.

No API keys, no daemon, no CI workers. The package depends on exactly
`pydantic`; everything else is self-authored. It runs offline.

```bash
git clone <your-fork> chimera && cd chimera
pip install -e .
chimera init          # setup wizard: identity, model tiers, memory, autonomy
chimera install-agents
chimera new "compare three approaches to X and recommend one"
chimera tick
```

## Setup

`chimera init` walks the four decisions that must be made before a first run,
writes a `.env`, then runs preflight. Re-run it any time; it backs up an
existing `.env` rather than replacing it.

`chimera init --check` runs the preflight alone and writes nothing. Use it to
answer "is this install sane?" — most importantly whether your maker and
critic tiers actually differ, which is the one misconfiguration the engine
cannot detect for you until a run is already underway.

```
  [ok  ] maker tier         opus
  [ok  ] critic tier        sonnet
  [ok  ] maker != checker   maker and critic tiers differ
  [ok  ] autonomy levers    width_max=3 phases_max=5 call_budget=40 repair_laps=1
  [ok  ] memory DB          ~/.chimera/memory.db
  [ok  ] git repo           /path/to/chimera
```

## How it works

```
chimera new "<ask>"            The only intake gate (G1). Parks with one
   [--shape straight|          question set if the ask is ambiguous — it asks
    diamond|pipeline]          once and never re-interviews. You pick the run
        │                      shape; the framework only recommends.
        ▼
chimera tick                   A session claims the task (claiming is a commit)
        │                      and drives the graph, executing each pending
        ▼                      agent call with its own Agent tool.
graph arc (autonomous)         The planner emits a DAG. Admission clamps it
  plan → admit → run           against your levers and refuses a plan that
        │                      breaks a rule, into one bounded re-plan lap.
        ▼
verify (3 critics, REFUTE)     Critics try to falsify the artifact. On genuine
        │                      refutation the maker gets one rewrite lap.
        ▼
digest                         Async surface: low-confidence flags, critic
        │                      splits, sign-off request.
        ▼
chimera approve                Sign-off (G2) — the only path to done. Workers
        │                      cannot declare their own work finished.
        ▼
archived                       Run summary captured to SQLite FTS5.
```

Two blocking human gates, intake and sign-off. Everything between is
background work.

### The DAG is data, the loop is code

A plan is phases of role-fenced nodes. Reads may only reference strictly
earlier phases, so a cycle is unrepresentable — you cannot express an infinite
loop in the plan format at all.

Iteration lives in Python instead, where it is bounded and countable: one
re-plan lap if admission refuses, verify repair laps on genuine refutation, and
an executor→maker repair lap when an executor pauses on work it read. Exhausting
a lap budget flags the digest; it never blocks.

### Six roles, fenced by capability

Capability derives from the tool grant, not from a label, so **write+shell and
write+network are unconstructible** — there is no way to express an agent that
can both edit files and reach the network.

Checker nodes see exactly `{ask, rubric, read artifacts}` and nothing else, and
they run on a model derived to be distinct from the producer they read — from
the model that producer was *actually dispatched on*, so changing a lever
between two ticks cannot quietly hand a checker its maker's model.

### maker ≠ checker, enforced in code

A verification panel whose critics share the maker's model is not adversarial;
it is the same model agreeing with itself. chimera refuses that panel rather
than running it and reporting a pass. This is why `chimera init` checks your
tiers before you ever open a task.

Critics never edit artifacts. They return an opinion; a maker acts on it.

### Autonomy levers

Every lever defaults to the restrictive value, widens exactly one rule, and has
a hard cap you cannot raise from the environment. A malformed value reads as
unset — a typo is not a decision.

| Lever | Default | Hard cap | Widens |
|---|---|---|---|
| `CHIMERA_GRAPH_WIDTH` | 3 | 8 | nodes per phase |
| `CHIMERA_GRAPH_PHASES` | 5 | 10 | phases per graph |
| `CHIMERA_GRAPH_CALL_BUDGET` | 40 | 250 | estimated calls per run |
| `CHIMERA_GRAPH_REPAIR_LAPS` | 1 | 3 | verify rewrite laps |

Model tiers (`CHIMERA_MAKER_MODEL`, `CHIMERA_CRITIC_MODEL`,
`CHIMERA_RESEARCH_MODEL`, `CHIMERA_JUDGE_MODEL`) resolve at call time, so a
change takes effect on the next command.

## Memory

An SQLite FTS5 store at `~/.chimera/memory.db` (`$CHIMERA_DB_PATH` overrides),
outside the repo by default so run history is never a commit. Arcs write a
run summary at terminal phases, including failures, and seed later runs from
prior ones. A degraded read fails open — missing memory slows a run down, it
does not stop it.

## Layout

```
src/chimera/
├── cli.py            # the command surface
├── setup_wizard.py   # chimera init
├── queue.py          # 7-state machine; every transition is a commit
├── gates.py          # G1 intake, G2 sign-off
├── graph.py          # plan admission, role fences, checker derivation
├── arcs/graph.py     # the run loop
├── verify/lite.py    # 3-critic REFUTE panel
├── roles.py          # capability fences
├── agents.py         # the six-role roster, model resolution
├── memory.py         # SQLite FTS5 store
└── prompts/          # runtime prompt data (package data — keep it here)
tests/                # 312 tests, no network, no credentials
```

## Commands

`chimera --help` is canonical.

| Command | Does |
|---|---|
| `init` | setup wizard; `--check` for preflight only |
| `install-agents` | write the six role files to `~/.claude/agents` |
| `new "<ask>"` | intake (G1) |
| `tick` | claim a runnable task, print pending calls |
| `arc submit` | return one agent result (or `--null`) |
| `approve` / `reject` | sign-off (G2) |
| `status` / `digest` | queue state, async surface |
| `archive` | capture to memory |

Tests: `python -m pytest -q`.

## Keeping private content out of the repo

chimera commits constantly and pushes what it commits, so anything written
into a tracked path will be published. The protection is **path-shaped**: the
private zones in `.gitignore` (`private/`, `scratch/`, `wip/`, `tasks/`,
`flows/*/output/`) are never tracked.

That means **where you write content is part of the control**. A file placed
outside those zones is a file that will be committed. If you work on material
that must not be published, add a local pre-push hook as a second layer —
path-based protection alone cannot inspect what a file contains.

The package holds no tokens. `.env` is gitignored, and `chimera init` never
writes a credential field.

## License

MIT.
