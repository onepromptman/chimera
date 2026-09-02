# Project: chimera

A git-durable background task engine for Claude Code sessions. The package
(`src/chimera/`) is a deterministic skeleton; a driving session is the runtime
(see `TICK_PROTOCOL.md`). No API keys, no cron, no CI workers.

Core method: loops over prompts, externalized memory, adversarial
verification, maker ≠ checker, throughput over latency.

**Where things live:** module responsibilities are documented in each module's
docstring (`src/chimera/*.py`, `src/chimera/arcs/*.py`). Read the docstring,
not a doc. Arc invariants are proven by the parity suites
(`tests/test_arc_*.py`, parametrized over `tests/arc_drivers.py::ARCS`); a new
arc ships only by registering there and going green.

## Security rules

1. **Path-shaped publish boundary.** chimera commits every transition and
   pushes what it commits, so anything in a tracked path gets published. The
   private zones in `.gitignore` are never tracked — which means *where*
   content is written is part of the control. The framework makes no judgment
   about what your content is. For material that must not be published, add a
   local pre-push hook as a second layer.
2. Never commit `.env`, `.mcp.json`, credentials, tokens, or API keys. The
   package holds no tokens and `chimera init` never writes a credential field.
3. Always HTTPS. Tokens are ephemeral; never hardcode one.
4. Validate input at trust boundaries with Pydantic constrained types.
   `models.py` is the single contract surface (`extra="forbid"`).
5. Never `eval()`, `exec()`, or `pickle.loads()` on untrusted data.
6. Never `yaml.load()` — chimera reads and writes its own constrained
   `questions.yaml` subset, with no PyYAML dependency.
7. Never `subprocess.run(shell=True)` with user input.
8. Pin dependencies exactly. Everything beyond `pydantic` and `pytest` is
   self-authored, so the dependency surface stays auditable.
9. Mask secrets in output (last 4 characters only).

## Architecture invariants

```
G1 intake (blocking) -> arc loop (autonomous, checkpoint-commit per step)
                        + verify (3-critic REFUTE) + digest (async)
                    -> G2 sign-off (blocking) -> done -> archived
```

- Exactly **two blocking human gates** (G1 intake-once, G2 sign-off).
- 7-state queue; every transition is a commit; `done` ONLY via `transition()`
  (verify gate + G2). Workers cannot self-declare done.
- **Verification is one tier**: 3 critics, REFUTE. On failure the arc runs one
  bounded critique→rewrite lap before halting.
- Null tolerance: a degraded agent call gets one retry, then `--null`; expiry
  routes as a recoverable null, never a new halt class. 250-call ceiling.
- The live arc surface is ONE: **graph**, the planner-emitted-DAG runtime. The
  DAG is data (phases of role-fenced nodes; reads reference strictly earlier
  phases, so cycles are unrepresentable); the loop is code (one re-plan lap on
  admission refusal, verify repair laps, and an executor→maker repair lap
  bounded by `_REPAIR_LAPS` — exhaustion flags the digest, never blocks).
  A repair lap invalidates the whole read closure of the maker it repairs, so
  a sibling that already approved now-deleted content re-runs rather than
  standing.
- `graph.admit()` clamps every plan against the autonomy levers, which are
  default-restrictive, read only in `levers.py`, and treat a typo as unset.
- Role fences (`roles.py`) derive capability from the tool grant: write+shell
  and write+network are unconstructible.
- Checker nodes see exactly {ask, rubric, read artifacts} (`graph.checker_brief`)
  and derive a model distinct from the producer they read — from the model that
  producer was ACTUALLY dispatched on (`node_models` in arc state), so lever
  drift between ticks cannot hand a checker its producer's model. Every
  non-checker role counts as a producer (a deny-list, so a role added later
  cannot silently fall out), and a `planner` node inside a plan is refused: the
  planner emits plans, it is never a node in one.
- **The operator picks the shape** (`chimera new --shape
  straight|diamond|pipeline`). The framework only recommends; every automatic
  shape router failed validation in both directions. A pick is enforced at
  admission; no pick means the planner proposes within the levers.
- **Search before draft:** arcs write run summaries at terminal phases
  (including failures) and seed from priors at start, failing open.

## Models (maker ≠ checker, enforced in code)

Makers run the maker tier; the fast tier and terminal critics run the critic
tier; the planner and read-less judges run the judge tier, which defaults to
the maker value so raising it can never collapse maker ≠ checker.

Resolution is **call-time** (`agents.resolve_models()`), overridable via
`CHIMERA_MAKER_MODEL` / `CHIMERA_CRITIC_MODEL` / `CHIMERA_RESEARCH_MODEL` /
`CHIMERA_JUDGE_MODEL`. `verify/lite.py` refuses a panel when the critic model
equals the maker model; a checker derivation with no distinct configured tier
refuses rather than inventing one. Critics never edit artifacts.

Setting the judge tier equal to the critic tier makes a read-less judge merge
critic verdicts on the critics' own model. No producer is involved, so it is
not a maker≠checker breach: admission emits `JUDGE_TIER_SHARES_CRITIC_MODEL`
and the flag rides the digest. It warns, it does not block.

The roster is exactly the six fenced roles (`agents.ROSTER == roles.FENCES`,
lockstep-tested). `CHIMERA_RESEARCH_MCP_TOOLS` grants MCP network tools to the
two read+web fences only — `mcp__*` classifies as network, so write+mcp stays
unconstructible, and any malformed entry reads the whole lever as unset.

## File organization

| Kind | Home |
|---|---|
| Role subagents | rendered from `agents.ROSTER` via `chimera install-agents` |
| Skills | `.claude/skills/<name>/SKILL.md` |
| Router telemetry | `audits/router-audit-YYYY-MM-DD.jsonl` (metadata only) |
| Private content | gitignored zones only — never relocate into tracked space |

**Fences:** `src/chimera/prompts/**.md` is runtime package data — never move it
out of the package. Keep the `audits/` name stable; the rotation script and the
checkpoint skill both glob it.

## Behavioral rules

1. **Durable state first.** Commit every transition and artifact immediately.
   Push fast-fails on permanent errors and backs off on transient ones;
   failures surface in `chimera status`.
2. **Ask once.** G1 questions post once and the task parks. Never loop an
   interview.
3. **Arcs are added on first real need**, and must prove invariant parity by
   registering in `tests/arc_drivers.py::ARCS` and going green.
4. **Decide autonomously.** Flags below `confidence: 70` ride the digest;
   they do not block.
5. **Root cause before fixes; plan before execute** (3+ files → numbered plan).
6. **Docs trail implementation.** No document may claim an invariant that has
   no test.

## Commands

`python -m chimera --help` is canonical. Non-obvious: `chimera init --check`
(preflight, writes nothing); `python -m pytest -q` (full suite; single test =
`python -m pytest tests/<file> -k name`); `install-agents` writes the six role
files. `ruff check src tests` and `mypy src` are dev-only.

## Notes

- The router hook is advisory and log-only; real validation is
  `routing.validate_selection()`.
- Deliberately absent: an API-key lane, tournament verification, and any
  dependency beyond pinned `pydantic` / `pytest`.
- Ad hoc CLI runs outside pytest write the real memory DB on archive (only the
  test suite auto-isolates). Set `CHIMERA_DB_PATH` before experimenting.
