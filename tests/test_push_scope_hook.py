"""The push-scope advisor hook (audit 2026-09-02, Q2): WARN-only, never blocks.

Runs the hook as a subprocess inside a throwaway git repo that mirrors the
`.claude/hooks/` layout, so REPO resolution (parents[2] of the hook file)
points at the temp repo, not this checkout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_SRC = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "push-scope-advisor.py"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def scoped_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".claude" / "hooks").mkdir(parents=True)
    shutil.copy(HOOK_SRC, repo / ".claude" / "hooks" / "push-scope-advisor.py")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "ok.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def _run_hook(repo: Path, command: str) -> dict:
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    proc = subprocess.run(
        [sys.executable, str(repo / ".claude" / "hooks" / "push-scope-advisor.py")],
        input=json.dumps(event),
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_framework_only_push_is_a_plain_allow(scoped_repo: Path) -> None:
    (scoped_repo / "src" / "more.py").write_text("y = 2\n")
    _git(scoped_repo, "add", "-A")
    _git(scoped_repo, "commit", "-q", "-m", "framework change")
    out = _run_hook(scoped_repo, "git push -u origin main")
    assert out == {"continue": True}


def test_plugin_push_is_a_plain_allow(scoped_repo: Path) -> None:
    """The shippable plugin is framework content. An allow-list without it warns
    on every plugin change, which trains the operator to ignore the alarm."""
    (scoped_repo / "plugins" / "chimera" / "skills" / "s").mkdir(parents=True)
    (scoped_repo / "plugins" / "chimera" / "skills" / "s" / "SKILL.md").write_text("---\n")
    (scoped_repo / ".claude-plugin").mkdir()
    (scoped_repo / ".claude-plugin" / "marketplace.json").write_text("{}\n")
    _git(scoped_repo, "add", "-A")
    _git(scoped_repo, "commit", "-q", "-m", "plugin change")
    out = _run_hook(scoped_repo, "git push origin main")
    assert out == {"continue": True}


def test_non_framework_path_warns_but_never_blocks(scoped_repo: Path) -> None:
    (scoped_repo / "apps" / "thing").mkdir(parents=True)
    (scoped_repo / "apps" / "thing" / "app.py").write_text("print('hi')\n")
    (scoped_repo / "tasks" / "20260101-x" / "artifacts").mkdir(parents=True)
    (scoped_repo / "tasks" / "20260101-x" / "artifacts" / "rows.csv").write_text("a,b\n1,2\n")
    _git(scoped_repo, "add", "-A")
    _git(scoped_repo, "commit", "-q", "-m", "non-framework content")
    out = _run_hook(scoped_repo, "git push origin main")
    assert out["continue"] is True  # advisory: the push proceeds
    assert "apps" in out["systemMessage"]
    assert "data files under tasks/" in out["systemMessage"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in out["hookSpecificOutput"]  # never a deny
    log = scoped_repo / ".claude" / "telemetry" / "push-scope.jsonl"
    record = json.loads(log.read_text().splitlines()[-1])
    assert record["outside"] == ["apps"] and record["data_under_tasks"] == 1


def test_non_push_commands_and_garbage_input_are_allowed(scoped_repo: Path) -> None:
    assert _run_hook(scoped_repo, "git status") == {"continue": True}
    proc = subprocess.run(
        [sys.executable, str(scoped_repo / ".claude" / "hooks" / "push-scope-advisor.py")],
        input="not json",
        cwd=scoped_repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(proc.stdout) == {"continue": True}


def test_git_dash_c_push_is_matched_and_non_push_commands_are_not(scoped_repo: Path) -> None:
    r"""`git -C <path> push` is a push. The old `git\s+push` regex only saw a
    push written immediately after `git`, so the whole check was skippable by
    spelling the command differently."""
    (scoped_repo / "apps").mkdir()
    (scoped_repo / "apps" / "app.py").write_text("print('hi')\n")
    _git(scoped_repo, "add", "-A")
    _git(scoped_repo, "commit", "-q", "-m", "non-framework content")
    fired = _run_hook(scoped_repo, "git -C /elsewhere push origin main")
    assert "apps" in fired["systemMessage"]
    assert _run_hook(scoped_repo, "git --no-pager push origin main")["continue"] is True
    for quiet in ("git status", "gitpush", "git log --oneline", "echo pushed"):
        assert _run_hook(scoped_repo, quiet) == {"continue": True}


def test_log_record_holds_exactly_the_allow_listed_fields(scoped_repo: Path) -> None:
    """No field allow-list meant whatever the record happened to carry was
    written verbatim. The keys are now fixed, whatever the payload says."""
    (scoped_repo / "apps").mkdir()
    (scoped_repo / "apps" / "app.py").write_text("print('hi')\n")
    _git(scoped_repo, "add", "-A")
    _git(scoped_repo, "commit", "-q", "-m", "non-framework content")
    marker = "CANARY-9f21-never-in-a-log-line"
    event = {
        "session_id": marker,
        "tool_name": "Bash",
        "transcript_path": f"/tmp/{marker}.jsonl",
        "tool_input": {"command": f"git push origin main # {marker}", "description": marker},
    }
    proc = subprocess.run(
        [sys.executable, str(scoped_repo / ".claude" / "hooks" / "push-scope-advisor.py")],
        input=json.dumps(event),
        cwd=scoped_repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["continue"] is True
    body = (scoped_repo / ".claude" / "telemetry" / "push-scope.jsonl").read_text()
    assert marker not in body
    record = json.loads(body.splitlines()[-1])
    assert set(record) == {"ts", "range", "outside", "data_under_tasks", "large"}


def test_large_file_is_sized_at_head_not_on_disk(scoped_repo: Path) -> None:
    """A file committed over the limit and truncated on disk afterwards still
    ships at its committed size. The 499 KB file pins the threshold from the
    other side, so moving LARGE_BYTES fails this test either way."""
    over = scoped_repo / "src" / "over.bin"
    under = scoped_repo / "src" / "under.bin"
    over.write_bytes(b"\0" * (501 * 1024))
    under.write_bytes(b"\0" * (499 * 1024))
    _git(scoped_repo, "add", "-A")
    _git(scoped_repo, "commit", "-q", "-m", "big blob")
    over.write_bytes(b"truncated")  # gone from the working tree, still in the commit

    out = _run_hook(scoped_repo, "git push origin main")
    assert out["continue"] is True
    assert "files over 500 KB" in out["systemMessage"]
    assert "src/over.bin" in out["systemMessage"]
    assert "src/under.bin" not in out["systemMessage"]
    record = json.loads(
        (scoped_repo / ".claude" / "telemetry" / "push-scope.jsonl").read_text().splitlines()[-1]
    )
    assert record["large"] == ["src/over.bin"]
    assert record["outside"] == []  # src/ is framework: only the size fired
