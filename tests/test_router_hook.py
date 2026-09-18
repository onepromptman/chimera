"""The PreToolUse:Agent hook is a counter, not a recorder (P1-5 / Q12).

The scrub is structural: prompt and description text is never assembled into
a telemetry record, so there is nothing downstream to strip. These tests run
the real hook file as a subprocess — the same way Claude Code invokes it —
against a copy whose log lands in tmp_path.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_SRC = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "router-enforcer.py"
MARKER = "CANARY-a4f1-do-not-log-this-prompt-text"


@pytest.fixture
def hook(tmp_path: Path) -> Path:
    """A copy of the hook whose LOG_PATH (derived from __file__) is under tmp."""
    dst = tmp_path / ".claude" / "hooks" / "router-enforcer.py"
    dst.parent.mkdir(parents=True)
    shutil.copy(HOOK_SRC, dst)
    return dst


def _log_path(hook: Path) -> Path:
    return hook.parents[2] / ".claude" / "telemetry" / "router-interceptions.jsonl"


def _run(hook: Path, event: dict) -> str:
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _event(**tool_input: object) -> dict:
    return {
        "session_id": "sid-test",
        "tool_name": "Agent",
        "tool_input": tool_input,
    }


def test_hook_allows_and_never_logs_prompt_text(hook: Path):
    out = _run(
        hook,
        _event(
            subagent_type="general-purpose",
            prompt=f"read {MARKER} and summarize it",
            description=f"description holding {MARKER}",
        ),
    )
    assert json.loads(out) == {"continue": True}

    log = _log_path(hook)
    body = log.read_text(encoding="utf-8")
    assert MARKER not in body
    rec = json.loads(body.splitlines()[-1])
    assert rec["decision"] == "allow-general"
    assert rec["subagent_type_requested"] == "general-purpose"
    assert set(rec) <= {"ts", "sid", "subagent_type_requested", "model", "decision"}


def test_hook_records_a_named_specialist_and_its_model(hook: Path):
    out = _run(
        hook,
        _event(subagent_type="maker", prompt=MARKER, model="opus"),
    )
    assert json.loads(out) == {"continue": True}

    rec = json.loads(_log_path(hook).read_text(encoding="utf-8").splitlines()[-1])
    assert rec["decision"] == "allow-named"
    assert rec["subagent_type_requested"] == "maker"
    assert rec["model"] == "opus"
    assert "prompt" not in rec and "prompt_excerpt" not in rec


def test_missing_subagent_type_reads_as_general(hook: Path):
    _run(hook, _event(prompt=MARKER))
    rec = json.loads(_log_path(hook).read_text(encoding="utf-8").splitlines()[-1])
    assert rec["subagent_type_requested"] == "general-purpose"
    assert rec["decision"] == "allow-general"
    assert "model" not in rec


def test_non_agent_tool_is_allowed_and_not_logged(hook: Path):
    out = _run(
        hook,
        {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": MARKER}},
    )
    assert json.loads(out) == {"continue": True}
    assert not _log_path(hook).exists()


@pytest.mark.parametrize("payload", ["", "   ", "not json at all {" + MARKER])
def test_hook_fails_open_on_garbage_input(hook: Path, payload: str):
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"continue": True}
    log = _log_path(hook)
    if log.exists():
        assert MARKER not in log.read_text(encoding="utf-8")


def test_identifier_fields_are_capped(hook: Path):
    """`subagent_type`, `session_id` and `model` are identifiers the caller
    controls. Uncapped, they were a way to smuggle prose into the log; each is
    now truncated at 64 chars where it is copied."""
    long_type = "x" * 400 + MARKER
    _run(
        hook,
        {
            "session_id": "s" * 500,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": long_type, "model": "m" * 500},
        },
    )
    rec = json.loads(_log_path(hook).read_text(encoding="utf-8").splitlines()[-1])
    assert MARKER not in json.dumps(rec)
    assert len(rec["subagent_type_requested"]) == 64
    assert len(rec["sid"]) == 64
    assert len(rec["model"]) == 64
    assert rec["decision"] == "allow-named"  # still classified, just not verbose


HOOK_FILES = (
    HOOK_SRC,
    HOOK_SRC.parent / "push-scope-advisor.py",
)


@pytest.mark.parametrize("path", HOOK_FILES, ids=lambda p: p.name)
def test_hooks_import_stdlib_only(path: Path):
    """CLAUDE.md: the hooks import nothing from the package. A hook runs on
    every tool call in every checkout, including ones where `chimera` is not
    installed — an import of it is a hook that dies (or, worse, half-runs)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import inside a hook is a package import
                modules.append("." * node.level + (node.module or ""))
            elif node.module:
                modules.append(node.module)
    assert modules, f"{path.name}: no imports parsed — did the file move?"
    roots = {m.split(".")[0] for m in modules}
    assert not any(r.startswith("chimera") or r == "" for r in roots), roots
    assert roots <= set(sys.stdlib_module_names) | {"__future__"}, roots
