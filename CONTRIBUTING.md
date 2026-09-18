# Contributing to chimera

Short by design. The invariants live in `CLAUDE.md`; this file is the mechanics
of getting a change in.

## One repo, one purpose

This origin carries the **chimera framework and nothing else — on any branch**.
No work content, no client or employer material, no personnel or
export-controlled data, no project that merely uses chimera. Work aimed at
another repo runs in *that* repo's checkout or worktree (`CLAUDE.md`
Behavioral Rule 7). A branch is not a hiding place: branches are pushed,
mirrored and audited like `main`.

## Boundary

The tracked half of the boundary is `.gitleaks.toml` (generic credentials plus
three repo-shaped rules) and the hooks in `.pre-commit-config.yaml`. Neither
the rules file nor the `boundary-tokens` hook names an employer, person, vendor
or internal tool: the *scanners* stay generic, because this repo has a public
mirror.

That is a claim about the scanners, not about the whole tree: the public
copy is sanitized by hand before each release.

The half that *does* name them is untracked and machine-local:

```
~/.config/chimera/leak-tokens.txt     # one regex per line; '#' starts a comment
```

The `boundary-tokens` pre-commit hook reads that file if it exists, greps your
staged files against it, and prints **filenames only** on a hit — never the
matched content, so nothing leaks into a terminal or a CI log. If the file does
not exist the hook is a silent no-op, so a fresh clone is never blocked by a
list it cannot see. `$CHIMERA_LEAK_TOKENS` overrides the path.

Populate it on any machine that touches work content; the same list backs the
optional machine-local pre-push guard and the `mirror-audit` skill.

`tasks/` is inside that boundary, not an exception to it. The queue is
git-durable, so every transition commits `tasks/<id>/task.json` plus the arc
state to this origin — the records are *engine state*, and leaving them
untracked would break the engine. What keeps them clean is G1 intake (a
framework ask only, per the rule above) and `tests/test_push_boundary.py`,
which fails if anything tracked under `tasks/` is not engine state: a task
directory with no loadable `task.json`, a filename no CLI verb writes, or a
staged blob over 256 KB — the arc state excepted, because it accumulates every
node's output and grows with the graph. The public mirror is sanitized at sync
time by the `mirror-audit` skill.

## Branches

- `claude/<topic>-<suffix>` — work done by a Claude Code session (the suffix is
  the session's, leave it).
- `<type>/<slug>` — hand or worktree work, where `<type>` is the Conventional
  Commits type: `chore/hygiene-2026-09-02`, `feat/graph-ledger`,
  `docs/adr-011`.

Worktrees belong to the target repo, and a chimera worktree is removed when its
branch merges.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): subject`, imperative, lower case, no trailing period.

Types in use: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`.
Common scopes: `ci`, `deps`, `contrib`, `graph`, `verify`, `cli`, `memory`.
The changelog is written by hand from these, so an off-convention subject costs
someone a read later.

## Running the checks locally

```bash
pip install pre-commit
pre-commit install          # once per clone
pre-commit run --all-files  # what CI will say, before you push
python scripts/gates.py     # the same gates, refusing to run on tool versions that differ from the CI pins
python scripts/gates.py --no-tests   # the static gates only
```

That is the whole local mirror of CI bar one: `pip-audit` has no local twin and
runs only in the `security` job.

CI also runs a `security` job on every push and PR: `pip-audit` over the
hash-locked closure (a known CVE in a pinned wheel fails the build; bump the
pin and re-lock) and `gitleaks` over the pushed commits with `.gitleaks.toml`
(a hit fails the build and names the file; rotate the secret, rewrite the
commit). Required check on `main` alongside `lint` and the test matrix.

## Automated review (subscription, no API key)

`.github/workflows/claude-review.yml` runs on every non-draft PR: the
official `code-review` plugin posts inline comments, then Claude Code's
bundled `/security-review` posts one security summary (including this
repo's boundary rules). It authenticates with a Claude subscription token:
run `claude setup-token` on your machine once and add the output as the
repository secret `CLAUDE_CODE_OAUTH_TOKEN`. Until the secret exists the
steps skip and the job passes. In a session, `/security-review` and
`/code-review` run the same checks on your branch before you push, and the
`security-guidance` plugin (enabled in `.claude/settings.json`) reviews each
change Claude makes as it makes it. Managed Code Review is Team/Enterprise
only; this is the Pro/Max path.

To have a cloud session babysit a PR (fix red CI, answer review comments),
ask it to "watch PR #N"; it subscribes to the PR's events and re-checks on a
timer until merge. `/skill-doctor` reports skill usage and context cost;
`claude plugin eval` (early access) evaluates a plugin's skills when enabled.

The ruff and mypy hooks are check-only, so a green hook means a green `lint`
job. Fix findings deliberately:

```bash
ruff check --fix src tests scripts .claude
ruff format src tests scripts .claude
mypy src
python -m pytest -q
```

Lint covers `scripts` and `.claude` because pre-commit lints every file it is
handed: anything CI skipped came back as a red hook on an otherwise clean tree.
`mypy` stays on `src` — the hooks are stdlib-only scripts, not the typed half
of the boundary.

To reproduce CI's environment exactly (hash-pinned, resolves nothing):

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
```

When you change a dependency in `pyproject.toml`, regenerate the lock in the
same commit (`uv` is a build tool here, not a project dependency):

```bash
pip install uv
uv pip compile pyproject.toml --extra dev --generate-hashes \
  --python-version 3.11 -o requirements.lock
```

CI's "Pins agree with the lock" step fails if the regenerated lock does not
carry every exact pin in `pyproject.toml` — `pip check` alone never sees the
`dev` extra, so it could not catch a drifted pytest/ruff/mypy pin.
`python scripts/gates.py` runs the same check locally.
Dependabot's pip updates edit `pyproject.toml` only — regenerate the lock on
those PRs before merging.

## Pull requests

Fill in `.github/PULL_REQUEST_TEMPLATE.md` — all of it. The two headings that
are not ceremony:

- **How tested** — paste the exact commands and their result lines. "Tests
  pass" is not evidence.
- **Boundary check** — four boxes, ticked because you looked.

Target **under 400 changed lines**. Bigger is allowed when it is honestly one
thing (a generated lockfile, a mechanical rename, a formatting pass) — say
which in the *Size* section so a reviewer knows which hunks need reading. A
1,000-line PR reviewed in seven minutes is a PR nobody reviewed.

All four checks must be green before merge: `test (3.11)`, `test (3.12)`,
`lint`, `security`.

## Releases

1. Bump `version` in `pyproject.toml`.
2. Add the top entry to `CHANGELOG.md` (Keep a Changelog format; semver rules
   for MAJOR / MINOR / PATCH are stated at the top of that file).
3. Commit: `chore(release): vX.Y.Z`.
4. Tag and push:

```bash
git tag vX.Y.Z
git push origin main --follow-tags   # or: git push origin vX.Y.Z
```

`pyproject.toml` + `CHANGELOG.md` are canonical for "what version is this";
tags have historically lagged, which is the thing to stop doing.

## Branch protection (owner action, one time)

CI cannot enforce itself. The owner must enable this in repo settings — nothing
in the tree can do it:

**`Settings → Branches → Add branch protection rule`**
(`https://github.com/onepromptman/chimera/settings/branches`; the equivalent
modern path is `Settings → Rules → Rulesets → New ruleset → New branch
ruleset`), with:

- Branch name pattern: `main`
- ☑ Require a pull request before merging
  - Required approvals: **0** — a solo owner cannot approve their own PR, so
    leave this at zero and do not tick "Require review from Code Owners";
    `.github/CODEOWNERS` is for review requests, not for gating.
- ☑ Require status checks to pass before merging
  - ☑ Require branches to be up to date before merging
  - Required checks: `test (3.11)`, `test (3.12)`, `lint`, `security`
- ☑ Require conversation resolution before merging
- ☑ Block force pushes
- ☑ Restrict deletions
- ☐ Allow bypassing the above settings — leave **off**; the point is that
  direct pushes to `main` stop, including the owner's.

Two related settings worth the same visit:

- **`Settings → Advanced Security`** (older label: *Code security and
  analysis*) → enable **Secret scanning** and **Push protection**, and
  **Private vulnerability reporting** (`SECURITY.md` points at it).
- **`Settings → Actions → General → Workflow permissions`** → *Read repository
  contents and packages permissions*. The workflow already declares
  `permissions: contents: read`; this makes it the default for anything added
  later.
