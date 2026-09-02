"""First-run setup: `chimera init`.

The engine reads all of its configuration from the environment, and every
lever defaults to the restrictive value (``levers.py``). That is the right
default for safety and the wrong default for a first run, because a new
operator has no way to see what the knobs are or whether their model tiers
satisfy maker != checker until an arc refuses mid-run.

This module closes that gap. It walks the operator through the four things
that must be decided before the first task -- identity, model tiers, where
memory lives, and how much autonomy the planner gets -- writes a ``.env``,
and then runs the same preflight checks as ``chimera init --check`` so a
misconfiguration surfaces here rather than at a node dispatch.

Two invariants this file must not break:

  - **It never writes outside the repo root and the chosen DB directory.**
    Role files go to ``~/.claude/agents`` only through ``agents.install_roles``,
    which the operator opts into explicitly.
  - **It never silently overwrites.** An existing ``.env`` is backed up, and
    in ``--non-interactive`` mode an existing file is left alone unless
    ``--force`` is passed. Setup that destroys prior configuration is worse
    than no setup.

The checks are the valuable half. ``--check`` is safe to run at any time and
writes nothing; it is the fastest way to answer "is this install sane?".
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import agents, levers

# The levers a fresh install should be asked about, in the order they matter.
# Each entry: (env var, prompt, default, hard cap, one-line blast radius).
_LEVER_PROMPTS = [
    (
        "CHIMERA_GRAPH_WIDTH",
        "Max nodes per phase",
        levers.GRAPH_WIDTH_DEFAULT,
        levers.GRAPH_WIDTH_HARD_MAX,
        "how wide a single phase may fan out",
    ),
    (
        "CHIMERA_GRAPH_PHASES",
        "Max phases per graph",
        levers.GRAPH_PHASES_DEFAULT,
        levers.GRAPH_PHASES_HARD_MAX,
        "how deep a plan may go",
    ),
    (
        "CHIMERA_GRAPH_CALL_BUDGET",
        "Estimated agent calls per run",
        levers.GRAPH_CALL_BUDGET_DEFAULT,
        levers.GRAPH_CALL_BUDGET_HARD_MAX,
        "the admission-time cost ceiling",
    ),
    (
        "CHIMERA_GRAPH_REPAIR_LAPS",
        "Verify critique->rewrite laps",
        levers.GRAPH_REPAIR_LAPS_DEFAULT,
        levers.GRAPH_REPAIR_LAPS_HARD_MAX,
        "how many times a refuted artifact may be rewritten",
    ),
]


def _ask(prompt: str, default: str) -> str:
    """Prompt with a default. Empty input keeps the default."""
    shown = f" [{default}]" if default else ""
    try:
        raw = input(f"  {prompt}{shown}: ").strip()
    except EOFError:  # piped stdin with nothing left -- take defaults
        return default
    return raw or default


def _ask_int(prompt: str, default: int, hard_max: int, why: str) -> int:
    """Prompt for a lever value, clamped to its hard cap.

    A value above the cap is clamped rather than rejected: the cap is not
    operator-adjustable, so refusing the input would only make the operator
    guess at the ceiling.
    """
    raw = _ask(f"{prompt} (max {hard_max} -- {why})", str(default))
    try:
        value = int(raw)
    except ValueError:
        print(f"    not a number, using {default}")
        return default
    if value > hard_max:
        print(f"    above the hard cap, clamped to {hard_max}")
        return hard_max
    if value < 1:
        print(f"    must be at least 1, using {default}")
        return default
    return value


def _git_identity() -> str:
    """Best-effort name for the default author, from git config."""
    try:
        out = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------------------
# Preflight checks -- read-only, safe to run any time
# ---------------------------------------------------------------------------

def run_checks(repo_root: Path) -> list[tuple[str, bool, str]]:
    """Return [(check name, ok, detail)] for the current environment.

    Never writes. The maker != checker check is the load-bearing one: it is
    the invariant that a misconfigured install breaks silently, because a
    same-model panel still *runs* -- it just is not adversarial.
    """
    results: list[tuple[str, bool, str]] = []

    models = agents.resolve_models()
    results.append(("maker tier", True, models.maker))
    results.append(("critic tier", True, models.critic))
    results.append(("research tier", True, models.research))
    results.append(("judge tier", True, models.judge))

    distinct = models.maker != models.critic
    results.append((
        "maker != checker",
        distinct,
        "maker and critic tiers differ"
        if distinct
        else f"BOTH are {models.maker!r} -- verify panels will refuse",
    ))

    # A judge on the critic tier is legal (a read-less judge reads no
    # producer) but worth surfacing, because it is easy to do by accident.
    if models.judge == models.critic:
        results.append((
            "judge tier",
            True,
            "shares the critic tier -- legal for read-less fan-in, but a "
            "judge that merges critic verdicts will run on their model",
        ))

    lv = levers.graph_levers()
    results.append((
        "autonomy levers",
        True,
        f"width_max={lv.width_max} phases_max={lv.phases_max} "
        f"call_budget={lv.call_budget} repair_laps={lv.repair_laps}",
    ))

    mcp = agents.research_mcp_tools()
    results.append((
        "MCP network grant",
        True,
        ", ".join(mcp) if mcp else "none (read+web fences get no MCP tools)",
    ))

    # Memory DB: importable and its directory writable.
    from .memory import DEFAULT_DB_PATH

    db = Path(os.environ.get("CHIMERA_DB_PATH") or DEFAULT_DB_PATH)
    parent_ok = db.parent.exists() or not db.parent.is_absolute()
    results.append((
        "memory DB",
        True,
        f"{db}{'' if parent_ok else '  (parent dir does not exist yet)'}",
    ))

    # Git: chimera's durability is git, so a non-repo is a hard failure.
    is_repo = (repo_root / ".git").exists()
    results.append((
        "git repo",
        is_repo,
        str(repo_root) if is_repo else f"{repo_root} is not a git repo -- "
        "chimera checkpoints every transition as a commit",
    ))

    if is_repo:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo_root), "remote"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            remotes = [r for r in out.stdout.split() if r]
        except (OSError, subprocess.SubprocessError):
            remotes = []
        results.append((
            "git remote",
            bool(remotes),
            ", ".join(remotes) if remotes
            else "none -- checkpoints commit locally but never push",
        ))

    return results


def print_checks(results: list[tuple[str, bool, str]]) -> bool:
    """Render check results. Returns True when every check passed."""
    width = max(len(name) for name, _, _ in results)
    all_ok = True
    for name, ok, detail in results:
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
        all_ok = all_ok and ok
    return all_ok


# ---------------------------------------------------------------------------
# The wizard
# ---------------------------------------------------------------------------

def _render_env(values: dict[str, str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# chimera configuration -- written by `chimera init`.",
        f"# Generated {stamp}. Safe to hand-edit; re-run `chimera init` to redo.",
        "#",
        "# Every value here is read from the environment at call time, so a",
        "# change takes effect on the next command with no reinstall.",
        "",
        "# --- identity -------------------------------------------------------",
        f"CHIMERA_AUTHOR={values['CHIMERA_AUTHOR']}",
        "",
        "# --- model tiers (maker != checker is enforced in code) --------------",
        f"CHIMERA_MAKER_MODEL={values['CHIMERA_MAKER_MODEL']}",
        f"CHIMERA_CRITIC_MODEL={values['CHIMERA_CRITIC_MODEL']}",
        f"CHIMERA_RESEARCH_MODEL={values['CHIMERA_RESEARCH_MODEL']}",
        f"CHIMERA_JUDGE_MODEL={values['CHIMERA_JUDGE_MODEL']}",
        "",
        "# --- memory ----------------------------------------------------------",
        f"CHIMERA_DB_PATH={values['CHIMERA_DB_PATH']}",
        "",
        "# --- autonomy levers (default-restrictive; each has a hard cap) -------",
    ]
    for env_name, _, _, hard_max, why in _LEVER_PROMPTS:
        lines.append(f"# {why} (hard cap {hard_max})")
        lines.append(f"{env_name}={values[env_name]}")
    mcp = values.get("CHIMERA_RESEARCH_MCP_TOOLS", "")
    lines += [
        "",
        "# --- MCP network grant for the two read+web fences --------------------",
        "# Comma-separated mcp__<server>__<tool> names. Any malformed entry",
        "# makes the whole lever read as unset.",
        f"CHIMERA_RESEARCH_MCP_TOOLS={mcp}",
        "",
    ]
    return "\n".join(lines)


def init(
    repo_root: Path,
    *,
    non_interactive: bool = False,
    force: bool = False,
    check_only: bool = False,
) -> int:
    """Run first-time setup. Returns a process exit code."""
    if check_only:
        print("chimera preflight\n")
        ok = print_checks(run_checks(repo_root))
        print()
        if not ok:
            print("Some checks failed. Run `chimera init` to fix configuration.")
            return 1
        print("All checks passed.")
        return 0

    env_path = repo_root / ".env"
    if env_path.exists() and non_interactive and not force:
        print(f"{env_path} already exists; refusing to overwrite.")
        print("Re-run with --force to replace it, or `chimera init` to edit interactively.")
        return 1

    print("chimera setup\n")
    print("Four things to decide. Press Enter to accept any default.\n")

    defaults = agents.resolve_models()
    from .memory import DEFAULT_DB_PATH

    values: dict[str, str] = {}

    if non_interactive:
        values["CHIMERA_AUTHOR"] = _git_identity() or "operator"
        values["CHIMERA_MAKER_MODEL"] = defaults.maker
        values["CHIMERA_CRITIC_MODEL"] = defaults.critic
        values["CHIMERA_RESEARCH_MODEL"] = defaults.research
        values["CHIMERA_JUDGE_MODEL"] = defaults.judge
        values["CHIMERA_DB_PATH"] = str(DEFAULT_DB_PATH)
        for env_name, _, default, _, _ in _LEVER_PROMPTS:
            values[env_name] = str(default)
        values["CHIMERA_RESEARCH_MCP_TOOLS"] = ""
    else:
        print("1. Identity -- stamped on every gate decision and commit.")
        values["CHIMERA_AUTHOR"] = _ask("Author name", _git_identity() or "operator")

        print("\n2. Model tiers. Makers author, critics refute. These MUST differ:")
        print("   a panel whose critic runs the maker's model is not adversarial,")
        print("   and chimera refuses it rather than pretending.")
        values["CHIMERA_MAKER_MODEL"] = _ask("Maker tier", defaults.maker)
        values["CHIMERA_CRITIC_MODEL"] = _ask("Critic tier", defaults.critic)
        values["CHIMERA_RESEARCH_MODEL"] = _ask("Research tier", defaults.research)
        values["CHIMERA_JUDGE_MODEL"] = _ask(
            "Judge tier (planner + read-less fan-in)", defaults.judge
        )

        print("\n3. Memory. An SQLite FTS5 store, outside the repo by default so")
        print("   run history is never a commit.")
        values["CHIMERA_DB_PATH"] = _ask("Memory DB path", str(DEFAULT_DB_PATH))

        print("\n4. Autonomy. Every lever defaults to the restrictive value;")
        print("   raising one widens exactly one rule, up to a fixed hard cap.")
        for env_name, prompt, default, hard_max, why in _LEVER_PROMPTS:
            values[env_name] = str(_ask_int(prompt, default, hard_max, why))

        print("\n   Optional: MCP tools for the two read+web roles (e.g. a search")
        print("   server). Comma-separated mcp__<server>__<tool>; blank for none.")
        values["CHIMERA_RESEARCH_MCP_TOOLS"] = _ask("MCP tools", "")

    if env_path.exists() and not force:
        backup = env_path.with_suffix(
            f".env.bak-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        shutil.copy2(env_path, backup)
        print(f"\nExisting .env backed up to {backup.name}")

    env_path.write_text(_render_env(values), encoding="utf-8")
    print(f"\nWrote {env_path}")

    # Apply now so the checks below reflect what was just chosen rather than
    # the pre-existing environment.
    for key, value in values.items():
        if value:
            os.environ[key] = value

    print("\nPreflight\n")
    ok = print_checks(run_checks(repo_root))

    print("\nNext")
    print("  1. Load the config:   set -a; . ./.env; set +a")
    print("  2. Install role files: chimera install-agents")
    print("  3. Open a task:        chimera new \"<your ask>\"")
    print("  4. Drive it:           chimera tick")
    if not ok:
        print("\nSome checks failed -- fix those before the first run.")
        return 1
    return 0
