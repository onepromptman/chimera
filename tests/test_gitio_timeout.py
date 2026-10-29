"""Every git call has a timeout; a timed-out push is transient, not permanent."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chimera import gitio


def test_run_passes_a_timeout_and_turns_expiry_into_git_error(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(gitio.GIT_TIMEOUT_ENV, raising=False)
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(gitio.subprocess, "run", fake_run)
    with pytest.raises(gitio.GitError) as excinfo:
        gitio._run(["git", "status"], cwd=tmp_path)
    assert seen["timeout"] == gitio.GIT_TIMEOUT_S
    assert "timed out" in excinfo.value.stderr


def test_timed_out_push_backs_off_and_retries(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(gitio.GIT_TIMEOUT_ENV, raising=False)
    attempts = []
    sleeps = []

    def fake_run(cmd, **kwargs):
        attempts.append(cmd)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(gitio.subprocess, "run", fake_run)
    monkeypatch.setattr(gitio.time, "sleep", sleeps.append)
    assert gitio.push(tmp_path, branch="main") is False
    assert len(attempts) == gitio.PUSH_RETRIES + 1
    assert sleeps == list(gitio.PUSH_BACKOFF_S)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("45", 45.0), ("2.5", 2.5), ("", 300), ("soon", 300), ("0", 300), ("-5", 300)],
)
def test_timeout_env_override(monkeypatch, raw: str, expected: float):
    monkeypatch.setenv(gitio.GIT_TIMEOUT_ENV, raw)
    assert gitio.git_timeout_s() == expected


def test_run_uses_the_env_timeout(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(gitio.GIT_TIMEOUT_ENV, "7")
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(gitio.subprocess, "run", fake_run)
    with pytest.raises(gitio.GitError) as excinfo:
        gitio._run(["git", "status"], cwd=tmp_path)
    assert seen["timeout"] == 7.0
    assert excinfo.value.stderr == "timed out after 7s"
