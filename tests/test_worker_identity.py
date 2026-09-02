"""Tests for chimera.cli._worker() — the actor stamp for queue mutations.

Resolution order:
  1. CHIMERA_AUTHOR    (preferred name; what new docs steer users to)
  2. CHIMERA_WORKER    (legacy; pre-2026-06-17 deployments may still set it)
  3. git config user.name
  4. session-<hostname>
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from chimera.cli import _worker


def test_chimera_author_wins(monkeypatch):
    monkeypatch.setenv("CHIMERA_AUTHOR", "test-author")
    monkeypatch.setenv("CHIMERA_WORKER", "ignored-legacy")
    assert _worker() == "test-author"


def test_chimera_worker_is_legacy_fallback(monkeypatch):
    monkeypatch.delenv("CHIMERA_AUTHOR", raising=False)
    monkeypatch.setenv("CHIMERA_WORKER", "legacy-session")
    assert _worker() == "legacy-session"


def test_git_config_used_when_no_env(monkeypatch):
    monkeypatch.delenv("CHIMERA_AUTHOR", raising=False)
    monkeypatch.delenv("CHIMERA_WORKER", raising=False)
    with patch("chimera.cli._git_config_user", return_value="test-author"):
        assert _worker() == "test-author"


def test_hostname_fallback_when_nothing_set(monkeypatch):
    monkeypatch.delenv("CHIMERA_AUTHOR", raising=False)
    monkeypatch.delenv("CHIMERA_WORKER", raising=False)
    with patch("chimera.cli._git_config_user", return_value=None):
        assert _worker() == f"session-{socket.gethostname()}"


def test_empty_env_vars_skip_to_next_source(monkeypatch):
    monkeypatch.setenv("CHIMERA_AUTHOR", "")
    monkeypatch.setenv("CHIMERA_WORKER", "")
    with patch("chimera.cli._git_config_user", return_value="from-git"):
        assert _worker() == "from-git"


def test_git_config_missing_does_not_raise(monkeypatch):
    monkeypatch.delenv("CHIMERA_AUTHOR", raising=False)
    monkeypatch.delenv("CHIMERA_WORKER", raising=False)
    # Simulate git not on PATH; helper should swallow and return None.
    with patch("chimera.cli.subprocess.run", side_effect=FileNotFoundError):
        from chimera.cli import _git_config_user

        assert _git_config_user() is None
        assert _worker() == f"session-{socket.gethostname()}"
