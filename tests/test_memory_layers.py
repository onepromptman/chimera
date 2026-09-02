"""Memory layers 1+2+3 coverage.

Layer 1 — extends memory.cmd_migrate with --source <dir> + --agent so the
harness auto-memory dir (a single flat dir with MEMORY.md) can be ingested
alongside the legacy per-agent layout. Existing per-agent migrate behavior
is unchanged.

Layer 2 — arc_memory.arc_write / arc_search is a universal adapter: any
arc_kind string is valid, no kinds are hardcoded anywhere. Same
.claude/memory.db, FTS5 index reused, dedup-on-write preserved.

Layer 3 — role_memory.role_write / role_search mirrors layer 2 shape but
keyed on (role, role_id). Sleeping infrastructure: no agents.py callable
wires to it yet; opt-in per role.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from chimera import arc_memory, role_memory
from chimera.memory import _connect, cmd_init, cmd_migrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migrate_flat(db_path: Path, source: Path, agent: str | None = None) -> None:
    cmd_migrate(
        argparse.Namespace(
            db=str(db_path),
            memory_root=None,
            source=str(source),
            agent=agent,
        )
    )


def _migrate_per_agent(db_path: Path, root: Path) -> None:
    cmd_migrate(
        argparse.Namespace(
            db=str(db_path),
            memory_root=str(root),
            source=None,
            agent=None,
        )
    )


def _all_rows(db_path: Path) -> list[dict]:
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM memories ORDER BY id")]


# ---------------------------------------------------------------------------
# Phase 1: flat-source sweep
# ---------------------------------------------------------------------------


def test_sweep_flat_source_ingests_bullets(tmp_path, capsys):
    """A flat dir with MEMORY.md becomes searchable user rows."""
    db = tmp_path / "m.db"
    src = tmp_path / "auto"
    src.mkdir()
    (src / "MEMORY.md").write_text(
        "- [Handoff one](project_handoff_one.md) — first handoff note\n"
        "- [Pref two](feedback_pref_two.md) — second note, feedback type\n",
        encoding="utf-8",
    )

    _init_db_silently(db)
    capsys.readouterr()  # clear cmd_init JSON

    with pytest.raises(SystemExit) as exc:
        _migrate_flat(db, src, agent="user")
    assert exc.value.code == 0
    capsys.readouterr()

    rows = _all_rows(db)
    assert len(rows) == 2
    titles = {r["title"] for r in rows}
    assert titles == {"Handoff one", "Pref two"}
    types = {r["title"]: r["type"] for r in rows}
    assert types["Pref two"] == "feedback"  # filename-inferred
    assert types["Handoff one"] == "project"
    assert all(r["agent"] == "user" for r in rows)
    assert all(r["arc_kind"] is None for r in rows)


def test_sweep_flat_source_default_agent_is_user(tmp_path, capsys):
    """--agent defaults to 'user' when omitted in flat mode."""
    db = tmp_path / "m.db"
    src = tmp_path / "auto"
    src.mkdir()
    (src / "MEMORY.md").write_text(
        "- [Solo entry](solo.md) — only entry\n", encoding="utf-8"
    )

    _init_db_silently(db)
    capsys.readouterr()
    with pytest.raises(SystemExit):
        _migrate_flat(db, src, agent=None)
    capsys.readouterr()

    rows = _all_rows(db)
    assert len(rows) == 1
    assert rows[0]["agent"] == "user"


def test_sweep_flat_source_missing_dir_errors(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        _migrate_flat(db, tmp_path / "missing", agent="user")
    assert exc.value.code == 1


def test_sweep_flat_source_missing_memory_md_errors(tmp_path, capsys):
    db = tmp_path / "m.db"
    src = tmp_path / "auto"
    src.mkdir()  # exists, but no MEMORY.md inside
    _init_db_silently(db)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        _migrate_flat(db, src, agent="user")
    assert exc.value.code == 1


def test_sweep_per_agent_mode_still_works(tmp_path, capsys):
    """Legacy per-agent layout (default mode) is unchanged."""
    db = tmp_path / "m.db"
    root = tmp_path / "agent-memory"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "MEMORY.md").write_text(
        "- [Alpha learning](pattern_alpha.md) — something alpha learned\n",
        encoding="utf-8",
    )
    (root / "beta").mkdir(parents=True)
    (root / "beta" / "MEMORY.md").write_text(
        "- [Beta learning](pattern_beta.md) — something beta learned\n",
        encoding="utf-8",
    )

    _init_db_silently(db)
    capsys.readouterr()
    with pytest.raises(SystemExit):
        _migrate_per_agent(db, root)
    capsys.readouterr()

    rows = _all_rows(db)
    assert len(rows) == 2
    agents = {r["agent"] for r in rows}
    assert agents == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# Phase 2: arc memory universal adapter
# ---------------------------------------------------------------------------


def test_arc_write_creates_row(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    row = arc_memory.arc_write(
        arc_kind="research",
        arc_id="r-001",
        title="judge-panel pattern",
        body="diverse-lens critics caught 30% more real bugs",
        tags="judge,panel",
        db_path=db,
    )
    assert row["agent"] == "arc"
    assert row["type"] == "pattern"
    assert row["arc_kind"] == "research"
    assert row["arc_id"] == "r-001"
    assert row["title"] == "judge-panel pattern"
    assert row["tags"] == "judge,panel"


def test_arc_write_accepts_unknown_kind(tmp_path):
    """The contract is universal — no kind is special-cased anywhere."""
    db = tmp_path / "m.db"
    _init_db_silently(db)
    # An arc kind that doesn't exist in the codebase today (and may never)
    row = arc_memory.arc_write(
        arc_kind="hypothetical-arc-x",
        arc_id="hx-1",
        title="proof",
        body="adapter took an unknown kind without complaint",
        db_path=db,
    )
    assert row["arc_kind"] == "hypothetical-arc-x"


def test_arc_write_dedup_on_kind_id_title(tmp_path):
    """Second write with same (kind, arc_id, title) updates; does not append."""
    db = tmp_path / "m.db"
    _init_db_silently(db)
    first = arc_memory.arc_write(
        arc_kind="design",
        arc_id="d-007",
        title="wireframe-first",
        body="v1 body",
        db_path=db,
    )
    second = arc_memory.arc_write(
        arc_kind="design",
        arc_id="d-007",
        title="wireframe-first",
        body="v2 body — updated",
        db_path=db,
    )
    assert first["id"] == second["id"]
    assert second["body"] == "v2 body — updated"

    rows = arc_memory.arc_search(arc_kind="design", arc_id="d-007", db_path=db)
    assert len(rows) == 1


def test_arc_search_filters_by_kind(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    for kind in ("research", "design", "hypothetical-arc-x"):
        arc_memory.arc_write(
            arc_kind=kind,
            arc_id=f"{kind}-1",
            title=f"{kind}-note",
            body=f"learned something about {kind}",
            db_path=db,
        )

    research = arc_memory.arc_search(arc_kind="research", db_path=db)
    assert len(research) == 1 and research[0]["arc_kind"] == "research"

    hypo = arc_memory.arc_search(arc_kind="hypothetical-arc-x", db_path=db)
    assert len(hypo) == 1 and hypo[0]["arc_kind"] == "hypothetical-arc-x"

    all_arcs = arc_memory.arc_search(db_path=db)
    assert len(all_arcs) == 3


def test_arc_search_fts_query_finds_arc_rows(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    arc_memory.arc_write(
        arc_kind="research",
        arc_id="r-1",
        title="judge-panel",
        body="wireframe stage was skipped and the critic refuted it",
        db_path=db,
    )
    arc_memory.arc_write(
        arc_kind="design",
        arc_id="d-1",
        title="hero-image",
        body="lorem ipsum unrelated content here",
        db_path=db,
    )

    hits = arc_memory.arc_search(query="critic", db_path=db)
    assert len(hits) == 1
    assert hits[0]["arc_kind"] == "research"


def test_arc_search_isolates_from_user_memory(tmp_path, capsys):
    """arc_search must not return user/feedback/project rows from layer 1."""
    db = tmp_path / "m.db"
    _init_db_silently(db)

    # Layer 1: insert a user-row via the flat sweep
    src = tmp_path / "auto"
    src.mkdir()
    (src / "MEMORY.md").write_text(
        "- [User note](project_x.md) — shared word: anchor\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        _migrate_flat(db, src, agent="user")
    capsys.readouterr()

    # Layer 2: insert an arc-row that also mentions 'anchor'
    arc_memory.arc_write(
        arc_kind="research",
        arc_id="r-1",
        title="arc note",
        body="the same anchor word in arc memory",
        db_path=db,
    )

    arc_hits = arc_memory.arc_search(query="anchor", db_path=db)
    assert len(arc_hits) == 1
    assert arc_hits[0]["agent"] == "arc"


def test_arc_write_rejects_blank_kind_or_id(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    with pytest.raises(ValueError):
        arc_memory.arc_write(
            arc_kind="", arc_id="r-1", title="t", body="b", db_path=db
        )
    with pytest.raises(ValueError):
        arc_memory.arc_write(
            arc_kind="research", arc_id="", title="t", body="b", db_path=db
        )
    with pytest.raises(ValueError):
        arc_memory.arc_write(
            arc_kind="has space", arc_id="r-1", title="t", body="b", db_path=db
        )


def test_summarize_run_uses_fixed_title(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    a = arc_memory.summarize_run(
        arc_kind="research", arc_id="r-1", summary="first take", db_path=db
    )
    b = arc_memory.summarize_run(
        arc_kind="research", arc_id="r-1", summary="revised", db_path=db
    )
    # Same id, body updated -> dedup-on-write worked through the convenience.
    assert a["id"] == b["id"]
    assert b["title"] == "run-summary"
    assert b["body"] == "revised"


def test_existing_db_without_arc_columns_is_upgraded(tmp_path, capsys):
    """A pre-Phase-2 DB (no arc_kind/arc_id columns) must be upgraded in place."""
    import sqlite3

    db = tmp_path / "m.db"
    # Hand-create a legacy-shape DB (no arc columns).
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            agent TEXT NOT NULL,
            type TEXT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            tags TEXT,
            source_file TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()

    arc_memory.arc_write(
        arc_kind="research",
        arc_id="r-1",
        title="post-upgrade",
        body="should have triggered ALTER TABLE",
        db_path=db,
    )

    with _connect(db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
    assert "arc_kind" in cols
    assert "arc_id" in cols


# ---------------------------------------------------------------------------
# Phase 3: role memory universal adapter (sleeping)
# ---------------------------------------------------------------------------


def test_role_write_creates_row(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    row = role_memory.role_write(
        role="contrarian-critic",
        role_id="global",
        title="schema-refute",
        body="grounding refutations in the JSON schema beat prose 3:1",
        tags="tactic,refute",
        db_path=db,
    )
    assert row["agent"] == "role"
    assert row["type"] == "pattern"
    assert row["role"] == "contrarian-critic"
    assert row["role_id"] == "global"
    assert row["title"] == "schema-refute"
    assert row["tags"] == "tactic,refute"


def test_role_write_accepts_unknown_role(tmp_path):
    """Same universal-adapter contract as arc_memory — any role string."""
    db = tmp_path / "m.db"
    _init_db_silently(db)
    row = role_memory.role_write(
        role="hypothetical-role-y",
        role_id="hy-1",
        title="proof",
        body="adapter took an unknown role without complaint",
        db_path=db,
    )
    assert row["role"] == "hypothetical-role-y"


def test_role_write_dedup_on_role_id_title(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    first = role_memory.role_write(
        role="simplifier", role_id="global", title="rule-one", body="v1", db_path=db
    )
    second = role_memory.role_write(
        role="simplifier", role_id="global", title="rule-one", body="v2", db_path=db
    )
    assert first["id"] == second["id"]
    assert second["body"] == "v2"

    rows = role_memory.role_search(role="simplifier", role_id="global", db_path=db)
    assert len(rows) == 1


def test_role_search_filters_by_role(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    for role in ("contrarian-critic", "simplifier", "hypothetical-role-y"):
        role_memory.role_write(
            role=role,
            role_id="global",
            title=f"{role}-note",
            body=f"something about {role}",
            db_path=db,
        )

    critic = role_memory.role_search(role="contrarian-critic", db_path=db)
    assert len(critic) == 1 and critic[0]["role"] == "contrarian-critic"

    hypo = role_memory.role_search(role="hypothetical-role-y", db_path=db)
    assert len(hypo) == 1 and hypo[0]["role"] == "hypothetical-role-y"

    all_roles = role_memory.role_search(db_path=db)
    assert len(all_roles) == 3


def test_role_search_fts_query_finds_role_rows(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    role_memory.role_write(
        role="contrarian-critic",
        role_id="global",
        title="refute-tactic",
        body="anchoring the refutation in the schema beat prose every time",
        db_path=db,
    )
    role_memory.role_write(
        role="simplifier",
        role_id="global",
        title="trim-pattern",
        body="lorem ipsum unrelated noise here",
        db_path=db,
    )

    hits = role_memory.role_search(query="schema", db_path=db)
    assert len(hits) == 1
    assert hits[0]["role"] == "contrarian-critic"


def test_role_search_isolates_from_user_and_arc(tmp_path, capsys):
    """role_search must return ONLY role rows, never user or arc rows."""
    db = tmp_path / "m.db"
    _init_db_silently(db)

    src = tmp_path / "auto"
    src.mkdir()
    (src / "MEMORY.md").write_text(
        "- [User note](project_x.md) — anchor word in user memory\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        _migrate_flat(db, src, agent="user")
    capsys.readouterr()

    arc_memory.arc_write(
        arc_kind="research",
        arc_id="r-1",
        title="arc note",
        body="the same anchor word in arc memory",
        db_path=db,
    )

    role_memory.role_write(
        role="contrarian-critic",
        role_id="global",
        title="role note",
        body="the same anchor word in role memory",
        db_path=db,
    )

    role_hits = role_memory.role_search(query="anchor", db_path=db)
    assert len(role_hits) == 1
    assert role_hits[0]["agent"] == "role"


def test_role_write_rejects_blank_role_or_id(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    with pytest.raises(ValueError):
        role_memory.role_write(role="", role_id="g", title="t", body="b", db_path=db)
    with pytest.raises(ValueError):
        role_memory.role_write(
            role="contrarian-critic", role_id="", title="t", body="b", db_path=db
        )
    with pytest.raises(ValueError):
        role_memory.role_write(
            role="has space", role_id="g", title="t", body="b", db_path=db
        )


def test_summarize_role_uses_fixed_title(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    a = role_memory.summarize_role(
        role="contrarian-critic", role_id="global", summary="first take", db_path=db
    )
    b = role_memory.summarize_role(
        role="contrarian-critic", role_id="global", summary="revised", db_path=db
    )
    assert a["id"] == b["id"]
    assert b["title"] == "role-summary"
    assert b["body"] == "revised"


def test_existing_db_without_role_columns_is_upgraded(tmp_path):
    """Pre-Phase-3 DB (no role/role_id columns) must be upgraded in place."""
    import sqlite3

    db = tmp_path / "m.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            agent TEXT NOT NULL,
            type TEXT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            tags TEXT,
            source_file TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()

    role_memory.role_write(
        role="contrarian-critic",
        role_id="global",
        title="post-upgrade",
        body="should have triggered ALTER TABLE for role columns",
        db_path=db,
    )

    with _connect(db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
    assert "role" in cols
    assert "role_id" in cols
    assert "arc_kind" in cols
    assert "arc_id" in cols


# ---------------------------------------------------------------------------
# Layer coexistence
# ---------------------------------------------------------------------------


def test_layer1_and_layer2_share_db_cleanly(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)

    # Layer 1
    src = tmp_path / "auto"
    src.mkdir()
    (src / "MEMORY.md").write_text(
        "- [User one](project_one.md) — one\n"
        "- [User two](feedback_two.md) — two\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        _migrate_flat(db, src, agent="user")
    capsys.readouterr()

    # Layer 2
    for n in range(3):
        arc_memory.arc_write(
            arc_kind="research",
            arc_id=f"r-{n}",
            title=f"finding {n}",
            body=f"learning number {n}",
            db_path=db,
        )

    rows = _all_rows(db)
    assert len(rows) == 5
    user_rows = [r for r in rows if r["agent"] == "user"]
    arc_rows = [r for r in rows if r["agent"] == "arc"]
    assert len(user_rows) == 2 and len(arc_rows) == 3


def test_all_three_layers_share_db_cleanly(tmp_path, capsys):
    """Layers 1 + 2 + 3 coexist in the same DB with no cross-contamination."""
    db = tmp_path / "m.db"
    _init_db_silently(db)

    src = tmp_path / "auto"
    src.mkdir()
    (src / "MEMORY.md").write_text(
        "- [User one](project_one.md) — one\n"
        "- [User two](feedback_two.md) — two\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        _migrate_flat(db, src, agent="user")
    capsys.readouterr()

    arc_memory.arc_write(
        arc_kind="research", arc_id="r-1", title="a", body="x", db_path=db
    )
    arc_memory.arc_write(
        arc_kind="research", arc_id="r-2", title="b", body="y", db_path=db
    )
    arc_memory.arc_write(
        arc_kind="design", arc_id="d-1", title="c", body="z", db_path=db
    )

    role_memory.role_write(
        role="contrarian-critic", role_id="global", title="t1", body="b1", db_path=db
    )
    role_memory.role_write(
        role="simplifier", role_id="global", title="t2", body="b2", db_path=db
    )

    rows = _all_rows(db)
    assert len(rows) == 7

    by_agent = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r)
    assert len(by_agent["user"]) == 2
    assert len(by_agent["arc"]) == 3
    assert len(by_agent["role"]) == 2

    assert len(arc_memory.arc_search(db_path=db)) == 3
    assert len(role_memory.role_search(db_path=db)) == 2


# ---------------------------------------------------------------------------
# Local helper that swallows the cmd_init JSON SystemExit
# ---------------------------------------------------------------------------


def _init_db_silently(db_path: Path) -> None:
    """cmd_init prints JSON then sys.exits 0; eat both to keep tests quiet."""
    try:
        cmd_init(argparse.Namespace(db=str(db_path)))
    except SystemExit as exc:
        assert exc.code == 0
