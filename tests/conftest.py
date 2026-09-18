import shutil
import subprocess
from pathlib import Path

import pytest

from chimera.queue import Queue


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)
    return proc.stdout


@pytest.fixture(scope="session")
def _repo_template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The work repo + its bare origin, built ONCE per session.

    Nine git subprocesses at ~130 ms; ~75 tests take the `repo` fixture, so
    paying it per test was ~10 s of the suite. Nothing ever runs against this
    pair — `repo` hands every test its own copy — so it stays pristine and
    the tests stay hermetic.
    """
    root = tmp_path_factory.mktemp("repo-template")
    origin = root / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")

    work = root / "work"
    work.mkdir()
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "test@chimera.local")
    _git(work, "config", "user.name", "chimera-test")
    # A developer with commit signing on globally (commit.gpgsign=true) would
    # otherwise have every fixture commit try to sign with their real key —
    # intermittently fatal ("failed to write commit object") and never what a
    # throwaway repo wants. The fixture owns its own identity; it owns this too.
    _git(work, "config", "commit.gpgsign", "false")
    _git(work, "remote", "add", "origin", str(origin))
    (work / ".gitignore").write_text(".chimera-tick.lock\n.claude/\n", encoding="utf-8")
    _git(work, "add", ".gitignore")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "-u", "origin", "main")
    return work, origin


@pytest.fixture
def repo(tmp_path: Path, _repo_template: tuple[Path, Path]) -> Path:
    """Throwaway git repo with a local bare origin (so push paths are real).

    A copy of the session template, not a fresh build: identity, gitignore,
    the initial commit and the upstream branch come with it. The remote URL is
    repointed at THIS test's own copy of the bare origin — without that, every
    test would push into the shared template and hermeticity would be gone.
    """
    work_src, origin_src = _repo_template
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    shutil.copytree(origin_src, origin)
    shutil.copytree(work_src, work)
    _git(work, "remote", "set-url", "origin", str(origin))
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
        "CHIMERA_AUTHOR",
        "CHIMERA_DB_PATH",
        "CHIMERA_GRAPH_WIDTH",
        "CHIMERA_GRAPH_PHASES",
        "CHIMERA_GRAPH_REPAIR_LAPS",
        "CHIMERA_VERIFY_MUTANT_CADENCE",
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
    return db
