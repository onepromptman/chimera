"""Tracked regression guard for the publish boundary.

The private zones (private/, scratch/, wip/, tasks/, flows/*/output/) must
never appear in the git index. Protection is structural — gitignored paths —
so this test is the committed tripwire: if a refactor or a mis-merge ever
tracks a file from a private zone, the suite fails immediately rather than
the content reaching a remote.

Keep this list in lockstep with .gitignore. A zone that is ignored but not
asserted here is a zone with no tripwire.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _find_repo_root() -> Path:
    """Walk up from this file until we find a directory containing pyproject.toml.

    Works whether pytest is run from the repo root, the worktree root, or a
    subdirectory.  Raises RuntimeError if the sentinel is not found.
    """
    candidate = Path(__file__).resolve().parent
    for _ in range(10):  # cap the walk; real monorepos are never 10 levels deep
        if (candidate / "pyproject.toml").is_file():
            return candidate
        candidate = candidate.parent
    raise RuntimeError(
        f"Could not locate pyproject.toml walking up from {Path(__file__).resolve()}"
    )


# Private-zone path prefixes (relative to repo root) that must never be tracked.
_WORK_ZONE_PREFIXES: tuple[str, ...] = (
    "private/",
    "scratch/",
    "wip/",
    "tasks/",
)

# Explicit path-filter expressions for git ls-files (pattern form for glob paths).
# flows/*/output/ has a wildcard, so we match it by prefix inspection after the fact.
_FLOWS_OUTPUT_PREFIX = "flows/"
_FLOWS_OUTPUT_SUFFIX = "/output/"


def _ls_files(repo_root: Path, *pathspecs: str) -> list[str]:
    """Return tracked filenames matching the given pathspecs (shell=False)."""
    proc = subprocess.run(
        ["git", "ls-files", "--", *pathspecs],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    return [line for line in proc.stdout.splitlines() if line.strip()]


def test_work_zones_contain_no_tracked_files() -> None:
    """No file from any work-zone directory may appear in the git index."""
    repo_root = _find_repo_root()

    # Query git for every prefix-based work zone in a single call.
    direct_hits = _ls_files(repo_root, *_WORK_ZONE_PREFIXES)

    # Query flows/ separately so we can filter to flows/*/output/ paths only.
    flows_candidates = _ls_files(repo_root, "flows/")
    flows_output_hits = [
        f for f in flows_candidates
        if f.startswith(_FLOWS_OUTPUT_PREFIX) and _FLOWS_OUTPUT_SUFFIX in f[len(_FLOWS_OUTPUT_PREFIX):]
    ]

    all_hits = direct_hits + flows_output_hits

    assert not all_hits, (
        "Security Rule #1 violation — the following work-zone files are tracked by git "
        "and would reach the public remote on push:\n"
        + "\n".join(f"  {h}" for h in all_hits)
    )


def test_gitignore_contains_flows_output_rule() -> None:
    """Sanity check: .gitignore must declare the flows/*/output/ ignore rule.

    If someone removes or renames the rule, this test surfaces the gap before
    any output files can accidentally land in the index.
    """
    repo_root = _find_repo_root()
    gitignore = repo_root / ".gitignore"

    assert gitignore.is_file(), ".gitignore must exist at the repo root"

    content = gitignore.read_text(encoding="utf-8")

    # Accept either the exact pattern or a broader superset (e.g. "flows/").
    has_rule = (
        "flows/*/output/" in content
        or "flows/" in content
    )
    assert has_rule, (
        ".gitignore is missing a rule covering flows/*/output/ — "
        "arc output could be accidentally tracked and pushed"
    )
