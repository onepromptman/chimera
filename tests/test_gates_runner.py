"""`scripts/gates.py` is the local stand-in for CI, so its own honesty matters.

It is not importable as a package module, so these tests load it from its
path — which is also the only way to prove the shipped file, not a copy, has
the behaviour claimed for it.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

GATES_PY = Path(__file__).resolve().parents[1] / "scripts" / "gates.py"


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chimera_gates_runner", GATES_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pin_parsing_fails_closed_on_an_environment_marker(runner: ModuleType):
    """A conditional pin is not a pin this runner can vouch for: which version
    is required depends on the interpreter. It is dropped, so the tool reads
    as unpinned and `pinned_versions` exits instead of guessing."""
    pins = runner.parse_pins(
        [
            'ruff==0.9.6; python_version<"3.12"',
            "mypy>=1.15.0",
            "pytest==9.0.3",
            "pydantic==2.13.4  ",
        ]
    )
    assert pins == {"pytest": "9.0.3", "pydantic": "2.13.4"}
    assert "ruff" not in pins and "mypy" not in pins


@pytest.mark.parametrize("have", ["0.9.6.post1", "0.9.6rc1", "0.9.6+local", "0.9.60"])
def test_version_compare_rejects_a_near_miss(runner: ModuleType, monkeypatch, have: str):
    """The old `(\\d+\\.\\d+\\.\\d+)` capture read all of these as 0.9.6 and
    vouched for a tool CI never runs."""
    monkeypatch.setattr(runner, "installed_version", lambda tool: have)
    with pytest.raises(SystemExit) as exc:
        runner.check_versions({t: "0.9.6" for t in runner.TOOLS})
    assert have in str(exc.value) and "0.9.6" in str(exc.value)


def test_version_compare_accepts_the_exact_pin(runner: ModuleType, monkeypatch):
    monkeypatch.setattr(runner, "installed_version", lambda tool: "0.9.6")
    runner.check_versions({t: "0.9.6" for t in runner.TOOLS})  # no SystemExit


def test_installed_version_reports_the_full_distribution_string(runner: ModuleType):
    for tool in runner.TOOLS:
        assert runner.installed_version(tool) == importlib.metadata.version(tool)


def test_no_tests_flag_is_parsed(runner: ModuleType):
    parser = runner.build_parser()
    assert parser.parse_args(["--no-tests"]).no_tests is True
    assert parser.parse_args([]).no_tests is False
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["-h"])
    assert exc.value.code == 0


def test_lint_paths_cover_scripts_and_hooks(runner: ModuleType):
    """`pre-commit` lints every file; CI lints LINT_PATHS. When they differ,
    `pre-commit run --all-files` is red on files CI never sees."""
    assert set(runner.LINT_PATHS) >= {"src", "tests", "scripts", ".claude"}


def test_requirements_lock_carries_every_pyproject_pin(runner: ModuleType):
    """`pip check` never evaluates the `dev` extra, so a drifted pytest/ruff/
    mypy pin passed CI while this runner refused it locally."""
    assert runner.lock_pin_drift() == []


def test_lock_drift_is_detected(runner: ModuleType, monkeypatch, tmp_path: Path):
    """The gate must actually fail when the lock lags a bumped pin."""
    lock = tmp_path / "requirements.lock"
    lock.write_text("ruff==0.9.6 \\\n    --hash=sha256:deadbeef\n", encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "project_pins", lambda: ({}, {"ruff": "0.9.7"}))
    assert runner.lock_pin_drift() == ["ruff==0.9.7"]
    monkeypatch.setattr(runner, "project_pins", lambda: ({}, {"ruff": "0.9.6"}))
    assert runner.lock_pin_drift() == []
