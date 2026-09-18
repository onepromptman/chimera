# Security

## Reporting a vulnerability

Report privately, never in a public issue or PR.

Use GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability** (owner: enable it once at
`Settings → Advanced Security → Private vulnerability reporting`). That opens a
draft advisory visible only to you and the maintainer.

Expect an acknowledgement within a week. There is no bug bounty; this is a
personal framework maintained by one person.

## Supported versions

The tip of `main` and the version in the top entry of `CHANGELOG.md`. Nothing
older is patched — upgrade instead.

## The boundary rule

chimera is a framework repo with a public mirror. Work content — anything from
a gitignored zone, and any personnel or export-controlled data — never enters
this origin, on any branch. Protection is layered and deliberately
path-shaped (its known residual is recorded in
ADR-006, kept in the maintainer's private repository):

1. the gitignored zones listed under "Local workspace zones" in `.gitignore` —
   content written there is invisible to version control;
2. `.gitleaks.toml` + the `boundary-tokens` hook in `.pre-commit-config.yaml`,
   which reads an untracked machine-local token list
   (`~/.config/chimera/leak-tokens.txt`) and is a no-op when it is absent;
3. optionally, a personal machine-local pre-push guard at
   `~/.config/chimera/pre-push-guard.py` (untracked; absent on a fresh
   machine).

If you find work content in this repo's history, that is a security report —
use the private path above, not an issue.

## The package holds no secrets

`src/chimera/` contains no tokens, keys or credentials, and never reads a
credential from the tree. The runtime is a Claude Code session
(`TICK_PROTOCOL.md`) that carries its own auth; chimera's own configuration is
environment variables only (`CHIMERA_*` levers — see `.env.example`, which
holds names and placeholders, never values). `.env*` and `.mcp.json` are
gitignored and additionally flagged by `.gitleaks.toml`.

Dependencies are pinned exactly in `pyproject.toml` and hash-pinned
transitively in `requirements.lock`; CI installs from the lock and resolves
nothing at run time except the version-pinned build backend (PEP 517 fetches
`build-system.requires` itself, outside the hash-locked closure).
