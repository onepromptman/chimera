#!/usr/bin/env python3
"""PreToolUse:Bash advisor — warns (never blocks) when a `git push` would carry
non-framework content to the chimera origin.

Operator ruling 2026-09-02 (audit Q2): "no non-framework content on this
origin, on any branch" is enforced by ALERT, not by deny — an unattended
session must never wedge on a prompt, and the fences stay the real control.

What it checks, for a Bash command that pushes (`git … push …`, so
`git -C /elsewhere push` counts — over-matching is free in a warn-only hook):
  - top-level paths touched by commits not yet on the upstream ref that are
    outside the framework allow-list below;
  - files added in those commits larger than 500 KB, sized from the BLOB AT
    HEAD (`git ls-tree -r -l HEAD`) — the working-tree copy can be truncated
    after the commit, and it is the commit that gets pushed. The working-tree
    stat is the fallback for a path HEAD does not carry;
  - data-file extensions (csv/xlsx/xls/docx/pdf/parquet/zip) anywhere under
    tasks/ (task dirs are the queue, not a dataset drop).
On a hit it returns {"continue": true, "systemMessage": ..., "hookSpecificOutput":
{"additionalContext": ...}} so both the human and the session see the warning,
and appends a structural record (paths + sizes, no content) to the gitignored
telemetry file. That record is filtered through the explicit _FIELDS allow-list
and the log rotates at 10 MB, both mirroring router-enforcer.py: free text
never reaches a log line, structurally rather than by hand-scan (Security
Rule 1). Any exception => {"continue": true} (fail-open).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOG_PATH = REPO / ".claude" / "telemetry" / "push-scope.jsonl"

FRAMEWORK_TOP = {
    "src",
    "tests",
    "decisions",
    "flows",
    "scripts",
    "design-system",
    "plugins",
    ".claude-plugin",
    "tasks",
    "digest",
    ".claude",
    ".github",
    "BACKLOG.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "CODEOWNERS",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "TICK_PROTOCOL.md",
    "pyproject.toml",
    "requirements.lock",
    "uv.lock",
    ".gitignore",
    ".env.example",
    ".pre-commit-config.yaml",
    ".gitleaks.toml",
}
DATA_EXT = {".csv", ".xlsx", ".xls", ".docx", ".pdf", ".parquet", ".zip", ".db"}
LARGE_BYTES = 500 * 1024

# Every key this hook may ever write, and the size at which the log rolls over
# — same two controls as router-enforcer.py. Free text (the command line, git
# stderr, exception messages) is not on the list and is never assembled into a
# record in the first place.
_FIELDS = ("ts", "range", "outside", "data_under_tasks", "large")
_LOG_MAX_BYTES = 10 * 1024 * 1024


def _run(args: list[str]) -> str:
    return subprocess.run(args, cwd=str(REPO), capture_output=True, text=True, timeout=20).stdout


def _allow(extra: dict | None = None) -> None:
    payload: dict = {"continue": True}
    if extra:
        payload.update(extra)
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def _log(record: dict) -> None:
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
            LOG_PATH.rename(LOG_PATH.with_suffix(".jsonl.1"))
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        safe = {k: record[k] for k in _FIELDS if k in record}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _head_sizes(names: list[str]) -> dict[str, int]:
    """Blob sizes as COMMITTED at HEAD — what the push would actually carry.

    A file committed at 4 MB and truncated on disk afterwards still ships at
    4 MB, so the working-tree stat is not the measurement; it is only the
    fallback for a path HEAD does not carry (deleted, or renamed away).
    `-z` keeps paths verbatim, so core.quotePath cannot mangle a name.
    """
    sizes: dict[str, int] = {}
    if not names:
        return sizes
    try:
        out = _run(["git", "ls-tree", "-r", "-l", "-z", "HEAD", "--", *names])
    except Exception:
        return sizes
    for rec in out.split("\0"):
        meta, sep, path = rec.partition("\t")
        if not sep:
            continue
        field = meta.split()[-1] if meta.split() else ""
        if field.isdigit():
            sizes[path] = int(field)
    return sizes


def _pending_range() -> str | None:
    """Commits about to leave: upstream..HEAD, or the whole branch if no upstream."""
    try:
        upstream = _run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        ).strip()
    except Exception:
        upstream = ""
    if upstream and not upstream.startswith("fatal"):
        return f"{upstream}..HEAD"
    base = _run(["git", "merge-base", "HEAD", "origin/main"]).strip()
    return f"{base}..HEAD" if base else None


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        if event.get("tool_name") != "Bash":
            _allow()
            return
        command = str((event.get("tool_input") or {}).get("command", ""))
        # `git -C <path> push`, `git --no-pager push`, `git push` — any git
        # invocation that also says push. Warn-only, so over-matching costs
        # nothing and missing a real push costs the whole check.
        if not re.search(r"\bgit\b.*\bpush\b", command):
            _allow()
            return
        rng = _pending_range()
        if not rng:
            _allow()
            return
        names = _run(["git", "diff", "--name-only", "--diff-filter=ACMR", rng]).split()
        outside = sorted(
            {n.split("/", 1)[0] for n in names if n.split("/", 1)[0] not in FRAMEWORK_TOP}
        )
        data_under_tasks = sorted(
            n for n in names if n.startswith("tasks/") and Path(n).suffix.lower() in DATA_EXT
        )
        head_sizes = _head_sizes(names)
        large: list[str] = []
        for n in names:
            size = head_sizes.get(n)
            if size is None:  # not at HEAD: fall back to the working tree
                p = REPO / n
                try:
                    size = p.stat().st_size if p.is_file() else None
                except OSError:
                    size = None
            if size is not None and size > LARGE_BYTES:
                large.append(n)
        if not (outside or data_under_tasks or large):
            _allow()
            return
        lines = [
            "push-scope advisory (warn-only, audit Q2): this push carries non-framework content."
        ]
        if outside:
            lines.append(
                f"  top-level paths outside the framework allow-list: {', '.join(outside)}"
            )
        if data_under_tasks:
            lines.append(
                f"  data files under tasks/: {len(data_under_tasks)} (e.g. {data_under_tasks[0]})"
            )
        if large:
            lines.append(f"  files over 500 KB: {', '.join(large[:5])}")
        lines.append(
            "  Work content belongs in its own repo (CLAUDE.md Security Rule 1; Behavioral Rule 7)."
        )
        msg = "\n".join(lines)
        _log(
            {
                "ts": time.time(),
                "range": rng,
                "outside": outside,
                "data_under_tasks": len(data_under_tasks),
                "large": large[:10],
            }
        )
        _allow(
            {
                "systemMessage": msg,
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg},
            }
        )
    except Exception:
        _allow()


if __name__ == "__main__":
    main()
