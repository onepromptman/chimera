<!--
Keep it short. Every heading below earns its place: the boundary check is the
one that protects a repo with a public mirror, and the size line is the one
that keeps a second reviewer honest. See CONTRIBUTING.md.
-->

## What

<!-- One or two sentences. What does this change do? -->

## Why

<!-- The problem, the finding, or the ADR/BACKLOG entry this closes. Link it. -->

## How tested

<!-- Paste the EXACT commands you ran and their result lines — not "tests pass".
     `python scripts/gates.py` runs all of them with the pinned tool versions.
     e.g.
       python scripts/gates.py                       -> gates: ALL GREEN
       python -m pytest -q                           -> 302 passed in 33.59s
       ruff check src tests scripts .claude          -> All checks passed!
       ruff format --check src tests scripts .claude -> 60 files already formatted
       mypy src                                      -> Success: no issues found in 24 source files
       pre-commit run --all-files                    -> all hooks passed
-->

## Risk + rollback

<!-- What breaks if this is wrong, and how it is undone (revert the commit? a
     lever set back to default? a state file to delete?). "None" is an answer,
     but say it deliberately. -->

## Boundary check

- [ ] No work content — nothing from a gitignored zone (see `.gitignore`), no personnel or export-controlled data, no client or employer specifics.
- [ ] No secrets — no `.env`, no `.mcp.json`, no tokens, keys or credentials; nothing masked-but-present.
- [ ] Framework paths only — this repo carries the chimera framework and nothing else, on any branch (work aimed at another repo runs in that repo).
- [ ] `pre-commit run --all-files` is clean, gitleaks and `boundary-tokens` included.

## Size

<!-- Lines changed (git diff --shortstat main...HEAD). Target < 400.
     Larger is allowed but must be explained here: a mechanical rename? a
     generated lockfile? a formatting pass? Say which, so a reviewer knows
     which hunks actually need reading. -->
