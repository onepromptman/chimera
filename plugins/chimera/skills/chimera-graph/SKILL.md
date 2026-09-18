---
name: chimera-graph
description: Use when the change is HARD TO UNDO — getting it wrong would mean migrating data, rewriting code that already depends on it, or retracting something already published. Architecture, schema and contract decisions, auth and trust boundaries, migrations, and irreversible dependency choices qualify, and so does a QUESTION about one. If it is cheap to undo — a routine edit, a refactor, a draft, a dependency bump, a lookup — do not use this, however important the topic sounds.
---

# Chimera — a constraint framework, in any repo

It runs **here, in whatever repo the session is already in**. No queue, no
ledger, no service. It never calls your code: it tells you what may not happen
and refuses when it does.

## What this is for

Frontier models decompose well. What a model still cannot do is check its own
output from genuinely fresh context. That is the only structural advantage this
buys, so **use it to verify, not to decompose.** A 15-node plan over a model
that would have planned it better itself is scaffolding that costs calls and
adds seams.

## Scope: the ladder is wider than the trigger

The `description` above fires this skill only on hard-to-undo work, and that is
deliberate — auto-firing on a rename is the fastest way to get uninstalled. But
Gates 0 and 1 below are worth running on ordinary work too. They are cheap, and
you invoke them by asking, not by tripping the trigger.

So: **the trigger guards Gate 2. Gates 0 and 1 are yours to ask for.**

## The ladder — run the cheapest falsifier first

Verification cost should track the cost of being wrong, and **the cheapest check
is also the one most likely to fail**, so it goes first. Do not open with three
critics.

```
        one strong maker
              │
   GATE 0  EXECUTE          1 call · executor · low effort   ← always
              │             red → repair → re-run. Never spend
              │             critics on something that does not run.
              │             Check the COUNT, not just the exit code:
              │             "0 collected" also exits 0. A dependency
              │             once shadowed this repo's tests/ package
              │             and pytest ran nothing while CI stayed green.
              ▼
   GATE 1  ANCHOR           1 call · critic                  ← usually
              │             every cited path grepped, every URL
              │             fetched. Catches confident fabrication,
              │             the most common failure there is.
              ▼
   GATE 2  FULL PANEL       3 calls · critics, fresh context ← irreversible only
                            the verdict rule below applies HERE
                            and only here.
```

**Gates 0 and 1 are checks, not votes** — one of them failing stops you outright.
The verdict rule needs two or more opinions and applies at Gate 2 alone.

**Where to stop:**

| The change | Stop after | Cost |
|---|---|---|
| Something ran and you will see the result | Gate 0 | 1 call |
| A claim, a plan, a doc others will act on | Gate 1 | 2 calls |
| Hard to undo: schema, auth, migration, money, public | Gate 2 | 4 calls |

Most work stops at Gate 0. That is the point — three critics on a change whose
tests already fail is pure waste, and three critics on a reversible change is
theatre you will stop paying for within a week.

## One-shotting a result, not a document

A panel over prose certifies that a reader found it plausible. To one-shot a
*result*, the loop has to close on execution:

```
plan → make → EXECUTE → repair → EXECUTE → then verify
                 ▲                    │
                 └──── at most 1 ─────┘
```

Two rules that make this work, both learned the hard way:

- **Re-plan at phase barriers, not up front.** A plan emitted before any work
  has run cannot react to what the work found. If the shape of the answer is
  unknown, plan one phase, run it, then plan the next.
- **Never let a reader claim a test passed.** Only `executor` holds a shell.
  A `critic` saying "the tests pass" is reporting the artifact's own claim back
  to you.

## Roles

Installing this plugin ships all six. Dispatch them by name with the Agent
tool — and if a name resolves to an agent you did not install, stop: a
same-named agent with a different grant is worse than none, because the panel
will run unfenced and say nothing. Their fences are the point: six
roles over four grants, and **write+shell and write+network are unconstructible**.

| Role | Grant | Use for |
|---|---|---|
| `planner` | read only | turn the ask into phases of fenced nodes |
| `researcher` | read + network | gather evidence on one slice, every claim anchored |
| `maker` | read + write | author the artifact or the diff |
| `executor` | read + **shell** | run the tests/commands, report verbatim |
| `critic` | read + network | REFUTE one artifact against one rubric |
| `judge` | read only | score parallel candidates, merge verdicts |

**maker ≠ checker is a rule about models, not fences.** Dispatch the checkers on
a different model than the maker. If they are the same model, the panel is
theatre — say so and stop.

## The panel

**Gate 2 only.** Run **three checkers in parallel**, each in its own fresh
context, each seeing only `{the ask, its own angle, the artifact}`. Never show a
checker the maker's reasoning, the plan, or the other checkers' findings.

**Never tell a checker how an earlier gate went.** Sequence the gates to save
cost, but pipe no verdict forward: a reader told "the tests passed" produces
a measurably different judgement, and it is the wrong one. Each checker gets
`{ask, angle, artifact}` and nothing about what any other check found.

Angle 1 — **general REFUTE**
> Try to REFUTE this artifact. Default `refuted=true` on uncertainty. Examine
> logic, internal consistency, whether the recommendation follows from the
> evidence, and what concrete scenario would falsify the central claim.

Angle 2 — **GREP-ANCHOR check**
> Verify every cited source. For file paths, grep/read and confirm the claim is
> present. For URLs, fetch and confirm. Any failed substantiation → `refuted=true`.
> Default `refuted=true` if you cannot verify within reasonable effort.

Angle 3 — **CONFIRM (executing)** — dispatch on `executor`, not `critic`
> Run the stated test/build/lint command. Report the **verbatim** output and exit
> code. Do not interpret, do not fix, do not summarize. **State the expected exit
> code before running each command**, then `refuted=true` if any command missed
> its expectation. Not every check expects zero — a probe that proves an absence
> succeeds by exiting non-zero.

**Prose is not an exemption — CONSTRUCT the executing check.** An artifact with
no test command still makes claims that a shell can settle, so turn its claims
into commands: grep the file paths it cites, fetch the URLs, run the rule it
says it implements and diff that against what it claims, re-measure every number
it calls "measured", parse the config it says is valid. A pure-prose plan was
checked this way at first use — the executor ran the verdict rule over every
enumerated case, forced the schema error the text claimed would be raised, and
re-counted the line totals, catching a "measured" figure that had drifted during
its own repair. **Two of those three defects were invisible to reading.**

Only when a claim genuinely cannot be executed, add **MISSING-SOURCE** as the
third angle (*"identify evidence that is MISSING; empirical claims with no
authoritative source cited → refuted=true; default refuted=true on
absence-of-evidence"*) — and say plainly in the verdict that **nothing
executed, so the panel certifies only that a reader found the prose plausible.**

Whichever third angle you run, apply the **disclosure rule**: a gap the artifact
ITSELF names as an open question is at most `material`, never `fatal`; a gap it
conceals or asserts past is `fatal`. Refuting a disclosed gap harder than a
hidden one teaches the maker to hide its open findings.

Each checker returns exactly this shape, which mirrors
`chimera.models.CriticOpinion`:

```json
{"refuted": true, "reason": "...", "severity": "fatal"}
```

`refuted` is a bool, defaulting **true on doubt**. `reason` is a string.
`severity` is `"fatal" | "material" | "cosmetic"` or may be omitted. There is no
`confidence` field and no `reasoning` field.

**Grade severity honestly, but know what it does.** `fatal` = the central claim
does not hold, or a cited fact is wrong in a way that changes the conclusion.
`material` = a real defect that must be fixed, but the conclusion survives.
`cosmetic` = a typo, a stale count, a formatting slip.

**Only `fatal` changes the verdict** — it fails the run on its own, so reserve
it. `material` and `cosmetic` are recorded for the digest and weigh exactly the
same in the tally: **any refutation counts**. So do not soften `refuted` to be
polite, and do not refute over a typo you could simply name in `reason`.

**Nothing enforces this outside the chimera repo — you do.** Inside the package
the model is `extra="forbid"` and an invented field raises `ValidationError`
(verified: `reasoning` and `confidence` both fail `extra_forbidden`). In any
other repo there is no Pydantic model in scope and no validation happens at all,
so a malformed opinion will pass silently unless you catch it. Keep the shape
anyway — it is what lets a panel run anywhere be tallied or replayed by the
package later. **If a checker returns a different shape, treat that opinion as
INVALID** — it does not count toward `valid` in the verdict below, which makes a
2-checker panel fail rather than a 3-checker panel pass on two. Do not coerce it.

## The verdict rule (do not improvise this)

```
valid = checkers that returned a parseable opinion

FAIL    if valid < 2                 (too thin to be signal)
FAIL    if any refuted `fatal`       (the veto, alone)
SURVIVE otherwise
```

**Only `fatal` changes the verdict.** `material` and `cosmetic` refutations are
recorded as open findings for the digest and decide nothing. File them
honestly anyway: do not soften a real defect to spare the verdict, and do not
inflate severity to `fatal` to force a block when the conclusion actually
survives.

**`fatal` is the exception, and the only one.** One reader saying the central
claim does not hold fails the run alone, at any checker count. Reserve it.

A proportional-majority rule — a checker-count fraction, requiring more than
half of the valid opinions to come back clean — ran here until 2026-09-16.
Four real runs showed every refutation that ever killed a run was graded
`material`, never `fatal`, by a checker whose own `reason` text said the
conclusion survived — the majority math was vetoing runs its own checkers
called fine. `chimera.verify.lite.survives` moved to the fatal-only rule
above, and this skill's verdict rule tracks it exactly. Its checker COUNT does
not: this skill runs **three** angles (below), the engine's panel runs
**two** — that is this skill's own choice for a panel run by hand in any repo,
not an engine mirror, and no sentence here claims otherwise.

## Repair

At most **one** lap by default: the maker sees the findings and fixes, the panel
re-runs. If it fails twice, stop and report — do not grind. Spend more laps only
when the work is genuinely open-ended, and say how many you spent.

## Output

Write the result as a markdown file **in the current repo** (`decisions/`,
`docs/`, or alongside the change) or as a PR comment. State plainly:

- the verdict and the tally (`survives: true — 3 valid, 2 unrefuted`)
- what each checker refuted, verbatim enough to act on
- **whether anything executed**, and its exit code
- what was repaired in the lap

## Guardrails

- Checkers see `{ask, rubric, artifact}` and nothing else.
- Never dispatch a checker on the maker's model. Today `maker`/`planner`/`judge`
  are `opus` and `critic`/`executor`/`researcher` are `sonnet`, so the default
  panel already satisfies this — but check, do not assume.
- Never let a `critic` claim a test passed — it holds no shell. Only `executor`
  output counts as a test result.
- Do not run this on routine work. It costs real calls; reserve it for changes
  that are expensive to get wrong.
