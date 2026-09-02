"""Git plumbing for durable state.

Durable-state-first: every queue transition and every
materialized artifact is committed immediately; push is best-effort with
exponential backoff so a network blip degrades to a later push, never to
lost work.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

PUSH_RETRIES = 4
PUSH_BACKOFF_S = (2, 4, 8, 16)

# Failure-type classification (F8 remediation): an identical retried push
# cannot succeed against these, so they fast-fail instead of paying the full
# 2+4+8+16s backoff. Backoff is reserved for genuinely transient failures
# (network blips, timeouts). Markers are matched case-insensitively against
# git's stderr.
PERMANENT_PUSH_MARKERS = (
    # no remote / wrong remote
    "does not appear to be a git repository",
    "no configured push destination",
    "repository not found",
    # auth: a retry sends the same (absent) credentials
    "authentication failed",
    "permission denied",
    "could not read username",
    "could not read password",
    # non-fast-forward (M6 rider): retrying the identical push is futile —
    # the branch needs reconciliation, which is an operator decision
    "non-fast-forward",
    "fetch first",
    "[rejected]",
)


class GitError(RuntimeError):
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


def repo_root(start: Path | None = None) -> Path:
    out = _run(["git", "rev-parse", "--show-toplevel"], cwd=start or Path.cwd())
    return Path(out.strip())


def _run(cmd: list[str], cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise GitError(f"{' '.join(cmd)} failed: {stderr}", stderr=stderr)
    return proc.stdout


def current_branch(root: Path) -> str:
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root).strip()


def commit(root: Path, paths: list[Path], message: str) -> str | None:
    """Stage paths and commit. Returns the commit sha, or None if nothing changed."""
    rels = [str(p.relative_to(root) if p.is_absolute() else p) for p in paths]
    _run(["git", "add", "-A", "--", *rels], cwd=root)
    staged = _run(["git", "diff", "--cached", "--name-only", "--", *rels], cwd=root)
    if not staged.strip():
        return None
    _run(["git", "commit", "-m", message, "--", *rels], cwd=root)
    return _run(["git", "rev-parse", "HEAD"], cwd=root).strip()


def _is_permanent_push_error(error: str) -> bool:
    """Match against git's stderr ONLY — never the echoed command line, or a
    branch named e.g. 'fix/non-fast-forward-doc' would classify every one of
    its transient failures as permanent."""
    lowered = error.lower()
    return any(marker in lowered for marker in PERMANENT_PUSH_MARKERS)


def push(root: Path, branch: str | None = None) -> bool:
    """Push, backing off only on transient failures. Returns False (never raises)
    on final failure. Permanent failures (no remote, auth denied,
    non-fast-forward — see PERMANENT_PUSH_MARKERS) fast-fail on the first
    attempt: retrying an identical push against them cannot succeed, and the
    blind 30s backoff was the F8 audit finding."""
    branch = branch or current_branch(root)
    for attempt in range(PUSH_RETRIES + 1):
        try:
            _run(["git", "push", "-u", "origin", branch], cwd=root)
            return True
        except GitError as exc:
            if _is_permanent_push_error(exc.stderr or str(exc)):
                return False
            if attempt < PUSH_RETRIES:
                time.sleep(PUSH_BACKOFF_S[min(attempt, len(PUSH_BACKOFF_S) - 1)])
    return False


def push_health(root: Path) -> dict:
    """Last-known push state for `chimera status` (M6 rider): a lost push race
    or a dead remote must be visible within one status call, not discovered by
    git archaeology. `unpushed_commits` counts HEAD ahead of the upstream ref
    (accurate to the last fetch/push — exactly the 'did my pushes land' signal)."""
    branch = current_branch(root)
    try:
        upstream = _run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=root,
        ).strip()
    except GitError:
        return {
            "branch": branch,
            "upstream": None,
            "unpushed_commits": None,
            "note": "no upstream configured — pushes are failing or never ran",
        }
    try:
        count = int(_run(["git", "rev-list", "--count", f"{upstream}..HEAD"], cwd=root).strip())
    except (GitError, ValueError):
        count = None
    health = {"branch": branch, "upstream": upstream, "unpushed_commits": count}
    if count:
        health["note"] = f"{count} commit(s) not on {upstream} — pushes may be failing"
    return health


def _remote_host(url: str) -> str | None:
    """Host portion of a git remote URL, or None if unparseable. Handles
    scp-style (git@host:owner/repo), ssh:// and https:// forms."""
    url = url.strip()
    if not url:
        return None
    if "://" in url:  # ssh://git@host/... or https://host/...
        after = url.split("://", 1)[1]
        authority = after.split("/", 1)[0]
        return authority.rsplit("@", 1)[-1].split(":", 1)[0] or None
    if "@" in url and ":" in url:  # scp-style git@host:owner/repo.git
        return url.split("@", 1)[1].split(":", 1)[0] or None
    return None


def push_remote_host(root: Path) -> str | None:
    """Host of origin's push URL (e.g. 'github.com'), or None if there is no
    remote / it can't be read. Used by the n8n local_only intake guard to
    refuse opening a work-only task in a public checkout."""
    try:
        url = _run(["git", "remote", "get-url", "--push", "origin"], cwd=root).strip()
    except GitError:
        return None
    return _remote_host(url)
