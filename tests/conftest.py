import subprocess
from pathlib import Path

import pytest

from chimera.queue import Queue


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Throwaway git repo with a local bare origin (so push paths are real)."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "test@chimera.local")
    _git(work, "config", "user.name", "chimera-test")
    _git(work, "remote", "add", "origin", str(origin))
    (work / ".gitignore").write_text(".chimera-tick.lock\n.claude/\n", encoding="utf-8")
    _git(work, "add", ".gitignore")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "-u", "origin", "main")
    return work


@pytest.fixture
def queue(repo: Path) -> Queue:
    return Queue(root=repo)


@pytest.fixture(autouse=True)
def _clean_chimera_levers(monkeypatch: pytest.MonkeyPatch):
    """Hermetic suite: a developer's real model/MCP/graph levers must never
    skew a test run. Tests that exercise a lever set it explicitly (their
    setenv runs after this delenv)."""
    for key in (
        "CHIMERA_MAKER_MODEL",
        "CHIMERA_CRITIC_MODEL",
        "CHIMERA_RESEARCH_MODEL",
        "CHIMERA_JUDGE_MODEL",
        "CHIMERA_RESEARCH_MCP_TOOLS",
        "CHIMERA_GRAPH_WIDTH",
        "CHIMERA_GRAPH_PHASES",
        "CHIMERA_GRAPH_CALL_BUDGET",
        "CHIMERA_GRAPH_REPAIR_LAPS",
        # `chimera init` applies its choices to os.environ so its own
        # preflight reflects them; clear those too so a wizard test cannot
        # skew a later one.
        "CHIMERA_AUTHOR",
        "CHIMERA_DB_PATH",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _isolated_memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every memory write/read at a per-test DB so the suite never
    touches the operator's real ~/.chimera/memory.db (arc L2 summaries and
    archive capture both write to DEFAULT_DB_PATH at call time)."""
    db = tmp_path / "test-memory.db"
    monkeypatch.setattr("chimera.memory.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("chimera.arc_memory.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("chimera.role_memory.DEFAULT_DB_PATH", db)
    return db
