"""Tests for the relocated default memory.db path + migrate-db subcommand.

The default moved from <repo>/.claude/memory.db to ~/.chimera/memory.db so
the DB survives `git clean`, branch switches, and gets real 0600 perms on
Linux/macOS. WSL drvfs ignores chmod — covered in the README/CLAUDE.md.

Resolution rules (see chimera.memory._resolve_default_db_path):
  1. $CHIMERA_DB_PATH wins.
  2. Else ~/.chimera/memory.db if it exists.
  3. Else legacy <repo>/.claude/memory.db if it exists (back-compat).
  4. Else ~/.chimera/memory.db (fresh install creates it there).
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest


def _reload_memory(monkeypatch, home, env_path=None):
    """Reload chimera.memory with a fresh $HOME (and optional CHIMERA_DB_PATH).

    Module-level constants are evaluated at import time, so monkeypatching
    the env after import would be a no-op — we have to reload.
    """
    # POSIX resolves Path.home() via $HOME; native Windows ignores $HOME and
    # reads %USERPROFILE% (then %HOMEDRIVE%%HOMEPATH%). Set all of them so the
    # monkeypatched home actually takes effect cross-platform — otherwise the
    # resolver falls through to the real user home and these tests are bogus.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOMEDRIVE", home.drive or "")
    monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive):] if home.drive else str(home))
    if env_path is not None:
        monkeypatch.setenv("CHIMERA_DB_PATH", str(env_path))
    else:
        monkeypatch.delenv("CHIMERA_DB_PATH", raising=False)
    import chimera.memory as m

    return importlib.reload(m)


def test_default_path_uses_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "mydb.sqlite"
    m = _reload_memory(monkeypatch, tmp_path, env_path=custom)
    assert m._resolve_default_db_path() == custom


def test_default_path_prefers_home_when_present(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".chimera").mkdir(parents=True)
    (home / ".chimera" / "memory.db").write_bytes(b"")
    m = _reload_memory(monkeypatch, home)
    expected = home / ".chimera" / "memory.db"
    assert m._resolve_default_db_path() == expected


def test_default_path_falls_back_to_legacy_during_transition(tmp_path, monkeypatch):
    # No ~/.chimera/memory.db, but the legacy repo path exists.
    home = tmp_path / "home"
    home.mkdir()
    m = _reload_memory(monkeypatch, home)
    # Stand up a legacy file in the actual LEGACY_DB_PATH the module resolved.
    legacy = m.LEGACY_DB_PATH
    try:
        legacy.parent.mkdir(parents=True, exist_ok=True)
        if not legacy.exists():
            legacy.write_bytes(b"")
            created = True
        else:
            created = False
        m2 = importlib.reload(m)
        assert m2._resolve_default_db_path() == legacy
    finally:
        if created:
            legacy.unlink()


def test_default_path_fresh_install_targets_new_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    m = _reload_memory(monkeypatch, home)
    expected = home / ".chimera" / "memory.db"
    # No legacy, no new home — resolver should still point at the new home.
    if not m.LEGACY_DB_PATH.exists():
        assert m._resolve_default_db_path() == expected


def test_migrate_db_moves_legacy_to_new_home(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    m = _reload_memory(monkeypatch, home)
    # Stand up a real SQLite file at a chosen "legacy" location.
    legacy = tmp_path / "repo" / ".claude" / "memory.db"
    legacy.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(legacy))
    conn.executescript("CREATE TABLE t(id INTEGER); INSERT INTO t VALUES (42);")
    conn.commit()
    conn.close()
    dest = home / ".chimera" / "memory.db"

    with pytest.raises(SystemExit) as exc:
        m.main(["migrate-db", "--src", str(legacy), "--dest", str(dest)])
    assert exc.value.code == 0
    assert dest.exists()
    assert not legacy.exists()
    assert (legacy.with_suffix(".db.migrated")).exists()

    conn2 = sqlite3.connect(str(dest))
    rows = conn2.execute("SELECT id FROM t").fetchall()
    conn2.close()
    assert rows == [(42,)]


def test_migrate_db_noop_when_neither_exists(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    m = _reload_memory(monkeypatch, home)
    src = tmp_path / "missing-legacy.db"
    dest = tmp_path / "missing-dest.db"
    with pytest.raises(SystemExit) as exc:
        m.main(["migrate-db", "--src", str(src), "--dest", str(dest)])
    assert exc.value.code == 0
    assert not src.exists()
    assert not dest.exists()


def test_migrate_db_refuses_if_dest_exists(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    m = _reload_memory(monkeypatch, home)
    src = tmp_path / "src.db"
    dest = tmp_path / "dest.db"
    src.write_bytes(b"a")
    dest.write_bytes(b"b")
    with pytest.raises(SystemExit) as exc:
        m.main(["migrate-db", "--src", str(src), "--dest", str(dest)])
    assert exc.value.code == 2  # refuses without --force


def test_migrate_db_force_overwrites_dest(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    m = _reload_memory(monkeypatch, home)
    src = tmp_path / "src.db"
    dest = tmp_path / "dest.db"
    src.write_bytes(b"NEW")
    dest.write_bytes(b"OLD")
    with pytest.raises(SystemExit) as exc:
        m.main(["migrate-db", "--src", str(src), "--dest", str(dest), "--force"])
    assert exc.value.code == 0
    assert dest.read_bytes() == b"NEW"
    assert not src.exists()


@pytest.fixture(autouse=True)
def _reset_memory_module():
    """Reload chimera.memory back to clean import state after each test."""
    yield
    import chimera.memory

    importlib.reload(chimera.memory)
