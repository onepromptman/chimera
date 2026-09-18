# Chimera

Adversarial verification for changes that are **hard to undo**.

```
/plugin marketplace add onepromptman/chimera
/plugin install chimera
```

Nothing else to install. No Python, no queue, no service. The plugin ships six
subagent definitions and two skills, and it runs in whatever repo you are already
in.

| Skill | Say | What runs |
|---|---|---|
| `chimera-check` | "check this", "gate 0 this" | do the work, then an `executor` runs it and a `critic` anchors every cited path and URL — 1–2 agent calls |
| `chimera-graph` | "run this through chimera", "panel this" — or let it fire on hard-to-undo changes | the full ladder, ending in the three-checker panel below — about 4 calls |

## What it does

One strong maker produces the artifact. Then **three independent checkers**
examine it in fresh context, each seeing only `{the ask, its own angle, the
artifact}` — never the maker's reasoning and never each other's findings. Only
a `fatal` refutation vetoes, on its own; every other refutation is recorded as
an open finding and decides nothing. At most one repair lap.

```
        one strong maker
                |
   three checkers — fresh context, different angles,
          at least ONE of them executing
                |
        bounded repair against what they found
```

The three angles are **general REFUTE** (default to refuted on uncertainty),
**GREP-ANCHOR** (verify every cited path and URL, refute on any failed
substantiation), and **CONFIRM** — run the claims and report the verbatim exit
code.

## Why this and not a review prompt

Three properties, and they are the whole product:

1. **The maker never checks its own work.** Checkers run on a different model,
   and the panel refuses to build if they match.
2. **Checkers cannot see the maker's reasoning.** Fresh context is the mechanism;
   reviewing twice in the same session measurably does not help.
3. **The verdict is a rule, not a judgement.**
   ```
   FAIL     if valid < 2
   FAIL     if any refuted `fatal`
   SURVIVE  otherwise
   ```
   You do not get to decide you are done. Only `fatal` fails the run, and it
   does so alone; `material`/`cosmetic` refutations are recorded, not voted.
   Three lines, no discretion.

The closest thing in the ecosystem is advisory: a skill the agent may follow or
skip. This one refuses.

## The six roles

Six fences over four grants. **write+shell and write+network are
unconstructible** — no role can both edit files and run commands.

| Role | Grant | Effort |
|---|---|---|
| `planner` | read only | xhigh |
| `researcher` | read + network | medium |
| `maker` | read + write | high |
| `executor` | read + **shell** | low |
| `critic` | read + network | high |
| `judge` | read only | xhigh |

These files are **generated** from `src/chimera/agents.py::ROSTER`, never edited
by hand; a test fails if they drift. Regenerate with:

```
PYTHONPATH=src python -m chimera install-agents --target plugins/chimera/agents
```

## When to use it

When getting it wrong means migrating data, rewriting code that already depends
on it, or retracting something published. Architecture and schema decisions, auth
boundaries, migrations, irreversible dependency choices — and questions about
those.

**Not** for routine edits, refactors, drafts, or dependency bumps, however
important the topic sounds. It costs real agent calls. The skill's own
description enforces this distinction; it is written to stay quiet.

## Honest limitations

- **Three checkers on one model is closer to one vote than three.** Correlated
  errors are the known weakness of same-family panels. Point one checker at a
  different model family if the stakes justify it.
- **The panel's precision is not yet measured** against a labelled corpus, so
  treat a pass as "no independent reader could refute it", not as proof.
- **Nothing validates the opinion shape outside the `chimera` package.** In-repo
  it is a Pydantic model with `extra="forbid"`; elsewhere it is a convention you
  keep.

The full reasoning, including the research these choices came from, is in
[`decisions/`](https://github.com/onepromptman/chimera/tree/main/decisions).

MIT.
