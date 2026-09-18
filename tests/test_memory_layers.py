"""Memory layers 1+2 coverage.

Layer 1 — extends memory.cmd_migrate with --source <dir> + --agent so the
harness auto-memory dir (a single flat dir with MEMORY.md) can be ingested
alongside the legacy per-agent layout. Existing per-agent migrate behavior
is unchanged.

Layer 2 — arc_memory.arc_write / arc_search is a universal adapter: any
arc_kind string is valid, no kinds are hardcoded anywhere. Same
.claude/memory.db, FTS5 index reused, dedup-on-write preserved.

Layer 3 (role memory) was REMOVED 2026-09-02 (audit P2-3 / Q8): 233 lines
plus ~60 of CLI plumbing with zero callers in eighteen months. Git history
keeps it; re-add from there on first real need.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from chimera import arc_memory, memory
from chimera.memory import _connect, cmd_init, cmd_migrate
from chimera.memory import main as memory_main

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
    (src / "MEMORY.md").write_text("- [Solo entry](solo.md) — only entry\n", encoding="utf-8")

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
        arc_memory.arc_write(arc_kind="", arc_id="r-1", title="t", body="b", db_path=db)
    with pytest.raises(ValueError):
        arc_memory.arc_write(arc_kind="research", arc_id="", title="t", body="b", db_path=db)
    with pytest.raises(ValueError):
        arc_memory.arc_write(arc_kind="has space", arc_id="r-1", title="t", body="b", db_path=db)


def test_append_run_summary_suffixes_each_attempt(tmp_path):
    """P1-6: the terminal writers append; they never clobber a prior attempt."""
    db = tmp_path / "m.db"
    _init_db_silently(db)
    first = arc_memory.append_run_summary(
        arc_kind="graph", arc_id="g-1", summary="attempt one", db_path=db
    )
    second = arc_memory.append_run_summary(
        arc_kind="graph", arc_id="g-1", summary="attempt two", db_path=db
    )
    assert first["title"] == "run-summary-1"
    assert second["title"] == "run-summary-2"
    assert first["id"] != second["id"]
    rows = arc_memory.arc_search(arc_kind="graph", arc_id="g-1", db_path=db)
    assert {r["body"] for r in rows} == {"attempt one", "attempt two"}


def test_append_run_summary_counts_per_arc_id(tmp_path):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    arc_memory.append_run_summary(arc_kind="graph", arc_id="g-1", summary="a", db_path=db)
    other = arc_memory.append_run_summary(arc_kind="graph", arc_id="g-2", summary="b", db_path=db)
    assert other["title"] == "run-summary-1"
    assert arc_memory.run_summary_count(arc_kind="graph", arc_id="g-1", db_path=db) == 1


# ---------------------------------------------------------------------------
# L6-F3 / L6-F4 (audit 2026-09-02) — the read commands, driven through
# argparse, on a machine with no memory DB yet: a framed error, exit 1, and
# no 0-byte file left behind.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["get"],
        ["get", "--agent", "chimera"],
        ["list"],
        ["search", "anything"],
        ["arc-search", "anything"],
    ],
    ids=["get", "get-filtered", "list", "search", "arc-search"],
)
def test_read_commands_on_a_missing_db_die_cleanly(tmp_path, capsys, argv):
    db = tmp_path / "nope" / "memory.db"
    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), *argv])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error: no memory DB yet at ")
    assert str(db) in err
    assert not db.exists(), "a read command must not create the DB it did not find"
    assert not db.parent.exists()


def test_read_commands_still_work_once_the_db_exists(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    with pytest.raises(SystemExit):
        memory_main(
            [
                "--db",
                str(db),
                "set",
                "--agent",
                "chimera",
                "--type",
                "pattern",
                "--title",
                "a title",
                "--body",
                "a body",
            ]
        )
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "list"])
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["rows"][0]["title"] == "a title"


# ---------------------------------------------------------------------------
# Prune (audit 2026-09-02, §5.2 proposal §6) — a retention policy: rows age
# out by updated_at, feedback/user are exempt unless named, --dry-run reports
# without deleting, and the FTS index never keeps a pruned row.
# ---------------------------------------------------------------------------


def _seed_row(db_path: Path, *, agent, mem_type, title, body, age_days: int) -> int:
    with _connect(db_path) as conn:
        conn.executescript(memory.SCHEMA_SQL)
        conn.executescript(memory.FTS_SYNC_SQL)
        cur = conn.execute(
            """INSERT INTO memories (agent, type, title, body, updated_at)
               VALUES (?, ?, ?, ?, datetime('now', ?))""",
            (agent, mem_type, title, body, f"-{age_days} days"),
        )
        conn.commit()
        return int(cur.lastrowid)


def _prune(db_path: Path, capsys, *argv) -> dict:
    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db_path), "prune", *argv])
    assert exc.value.code == 0, capsys.readouterr().err
    return json.loads(capsys.readouterr().out)


def _titles(db_path: Path) -> set[str]:
    with _connect(db_path) as conn:
        return {r["title"] for r in conn.execute("SELECT title FROM memories")}


def _seed_mixed(db_path: Path) -> None:
    _seed_row(
        db_path,
        agent="arc",
        mem_type="pattern",
        title="old pattern",
        body="stale run summary",
        age_days=200,
    )
    _seed_row(
        db_path,
        agent="arc",
        mem_type="pattern",
        title="new pattern",
        body="fresh run summary",
        age_days=1,
    )
    _seed_row(
        db_path,
        agent="user",
        mem_type="feedback",
        title="old feedback",
        body="always cite sources",
        age_days=400,
    )
    _seed_row(
        db_path,
        agent="user",
        mem_type="user",
        title="old user note",
        body="prefers terse output",
        age_days=400,
    )
    _seed_row(
        db_path,
        agent="chimera",
        mem_type="project",
        title="old project",
        body="project decision",
        age_days=200,
    )


def test_prune_deletes_by_age_and_spares_feedback_and_user(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    capsys.readouterr()
    _seed_mixed(db)

    out = _prune(db, capsys, "--older-than-days", "30")
    assert out["deleted"] == 2
    assert out["by_type"] == {"pattern": 1, "project": 1}
    assert sorted(out["protected_types"]) == ["feedback", "user"]
    assert _titles(db) == {"new pattern", "old feedback", "old user note"}


def test_prune_dry_run_deletes_nothing(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    capsys.readouterr()
    _seed_mixed(db)

    out = _prune(db, capsys, "--older-than-days", "30", "--dry-run")
    assert out["dry_run"] is True
    assert out["matched"] == 2 and out["deleted"] == 0
    assert len(_titles(db)) == 5


def test_prune_type_overrides_the_protected_exemption(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    capsys.readouterr()
    _seed_mixed(db)

    out = _prune(db, capsys, "--older-than-days", "30", "--type", "feedback")
    assert out["deleted"] == 1
    assert out["protected_types"] == []
    assert "old feedback" not in _titles(db)
    assert "old pattern" in _titles(db)  # --type narrows to exactly one type


def test_prune_keeps_the_fts_index_in_sync(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    capsys.readouterr()
    _seed_row(
        db,
        agent="arc",
        mem_type="pattern",
        title="old pattern",
        body="obsoletetoken lives here",
        age_days=200,
    )

    with _connect(db) as conn:
        hits = conn.execute(
            "SELECT COUNT(*) AS n FROM memories_fts WHERE memories_fts MATCH ?",
            ("obsoletetoken",),
        ).fetchone()["n"]
    assert hits == 1

    _prune(db, capsys, "--older-than-days", "30")

    with _connect(db) as conn:
        hits = conn.execute(
            "SELECT COUNT(*) AS n FROM memories_fts WHERE memories_fts MATCH ?",
            ("obsoletetoken",),
        ).fetchone()["n"]
    assert hits == 0, "FTS index still matches a pruned row"


def test_prune_rejects_bad_arguments(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "prune", "--older-than-days", "0"])
    assert exc.value.code == 1
    assert "--older-than-days must be >= 1" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "prune", "--older-than-days", "30", "--type", "bogus"])
    assert exc.value.code == 1
    assert "invalid --type" in capsys.readouterr().err


def test_prune_on_a_missing_db_dies_cleanly(tmp_path, capsys):
    db = tmp_path / "nope" / "m.db"
    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "prune", "--older-than-days", "30"])
    assert exc.value.code == 1
    assert "no memory DB yet at" in capsys.readouterr().err
    assert not db.exists()


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
# Layer coexistence
# ---------------------------------------------------------------------------


def test_layer1_and_layer2_share_db_cleanly(tmp_path, capsys):
    db = tmp_path / "m.db"
    _init_db_silently(db)

    # Layer 1
    src = tmp_path / "auto"
    src.mkdir()
    (src / "MEMORY.md").write_text(
        "- [User one](project_one.md) — one\n- [User two](feedback_two.md) — two\n",
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

    # arc_search sees every arc row across kinds and no layer-1 row.
    assert len(arc_memory.arc_search(db_path=db)) == 3


# ---------------------------------------------------------------------------
# Local helper that swallows the cmd_init JSON SystemExit
# ---------------------------------------------------------------------------


def _init_db_silently(db_path: Path) -> None:
    """cmd_init prints JSON then sys.exits 0; eat both to keep tests quiet."""
    try:
        cmd_init(argparse.Namespace(db=str(db_path)))
    except SystemExit as exc:
        assert exc.code == 0
