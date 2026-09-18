"""Memory synthesis — the store compacts toward decisions.

Two halves of one thesis:

synthesize  Aged `arc` rows cluster STRUCTURALLY — (agent, type, arc_kind,
            the YYYY-MM month of updated_at), nothing parsed out of the title
            — and a cluster of two or more collapses into one `digest`-tagged
            row per part, chunked at 4,000 chars so no member is ever dropped.
            The members go and VACUUM hands the pages back. Singletons are
            left alone; `feedback`/`user` rows, rows tagged `decision`, and
            rows tagged `digest` (synthesis' own output — the dedup rule) are
            never candidates; a non-`arc` agent needs `--agent NAME`.

decision    A row tagged with the whole-word token `decision` ranks ahead of
            every other hit — in `search_general`, in the `search` CLI, and
            inside the priors block the planner reads, where the 1,500-char
            cap makes ordering load-bearing.

The refuted rule (title prefix minus trailing digits) is what this file now
forbids: `test_identity_numbered_titles_are_never_merged_away` and
`test_clusters_split_by_month` are its two gravestones.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from chimera import memory
from chimera.arcs import _common
from chimera.memory import _connect, cmd_init
from chimera.memory import main as memory_main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_db(db_path: Path) -> Path:
    """cmd_init prints JSON then sys.exits 0; eat both to keep tests quiet."""
    try:
        cmd_init(argparse.Namespace(db=str(db_path)))
    except SystemExit as exc:
        assert exc.code == 0
    return db_path


def _seed(
    db_path: Path,
    *,
    title: str,
    body: str = "a body line",
    agent: str = "arc",
    mem_type: str | None = "pattern",
    tags: str | None = None,
    arc_kind: str | None = None,
    arc_id: str | None = None,
    age_days: int = 200,
) -> int:
    with _connect(db_path) as conn:
        conn.executescript(memory.SCHEMA_SQL)
        conn.executescript(memory.FTS_SYNC_SQL)
        memory._ensure_arc_columns(conn)
        cur = conn.execute(
            """INSERT INTO memories
               (agent, type, title, body, tags, arc_kind, arc_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', ?))""",
            (agent, mem_type, title, body, tags, arc_kind, arc_id, f"-{age_days} days"),
        )
        conn.commit()
        return int(cur.lastrowid)


def _seed_many(db_path: Path, rows: list[dict]) -> None:
    """Bulk form of _seed — same columns, one connection."""
    with _connect(db_path) as conn:
        conn.executescript(memory.SCHEMA_SQL)
        conn.executescript(memory.FTS_SYNC_SQL)
        memory._ensure_arc_columns(conn)
        conn.executemany(
            """INSERT INTO memories
               (agent, type, title, body, tags, arc_kind, arc_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', ?))""",
            [
                (
                    r.get("agent", "arc"),
                    r.get("mem_type", "pattern"),
                    r["title"],
                    r.get("body", "a body line"),
                    r.get("tags"),
                    r.get("arc_kind"),
                    r.get("arc_id"),
                    f"-{r.get('age_days', 200)} days",
                )
                for r in rows
            ],
        )
        conn.commit()


def _legacy_db(db_path: Path) -> Path:
    """A store written before the arc columns and the FTS triggers existed."""
    conn = sqlite3.connect(str(db_path))
    try:
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
        conn.executemany(
            """INSERT INTO memories (agent, type, title, body, updated_at)
               VALUES ('arc', 'pattern', ?, ?, datetime('now', '-200 days'))""",
            [(f"run-summary-{n}", f"attempt {n}") for n in (1, 2, 3)],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _synth(db_path: Path, capsys, *argv) -> dict:
    """Run synthesize through the real argparse entry point."""
    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db_path), "synthesize", *argv])
    assert exc.value.code == 0, capsys.readouterr().err
    return json.loads(capsys.readouterr().out)


def _titles(db_path: Path) -> set[str]:
    with _connect(db_path) as conn:
        return {r["title"] for r in conn.execute("SELECT title FROM memories")}


def _digest_bodies(db_path: Path) -> dict[str, str]:
    with _connect(db_path) as conn:
        return {
            r["title"]: r["body"]
            for r in conn.execute("SELECT title, body FROM memories WHERE title LIKE 'digest:%'")
        }


def _row(db_path: Path, title_like: str) -> dict:
    with _connect(db_path) as conn:
        found = conn.execute("SELECT * FROM memories WHERE title LIKE ?", (title_like,)).fetchone()
    assert found is not None, f"no row matching {title_like!r}"
    return dict(found)


def _period(db_path: Path, age_days: int) -> str:
    """The YYYY-MM bucket a row seeded at *age_days* lands in."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT strftime('%Y-%m', datetime('now', ?)) AS p", (f"-{age_days} days",)
        ).fetchone()
    return str(row["p"])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_mixed(db_path: Path) -> None:
    """Three aged arc run summaries, plus every kind of exempt row."""
    for n in (1, 2, 3):
        _seed(
            db_path,
            title=f"run-summary-{n}",
            body=f"attempt {n} said something\nsecond line",
            tags="terminal",
            arc_kind="graph",
            arc_id=f"t-{n}",
        )
    _seed(db_path, title="old feedback", agent="user", mem_type="feedback", age_days=400)
    _seed(db_path, title="old user note", agent="user", mem_type="user", age_days=400)
    _seed(
        db_path,
        title="the storage call",
        body="we chose sqlite",
        agent="user",
        mem_type="project",
        tags="decision",
        age_days=400,
    )


def _graph_digest_title(db_path: Path, age_days: int = 200) -> str:
    return f"digest:arc|pattern|graph|{_period(db_path, age_days)}:part-1"


def _row_count(db_path: Path) -> int:
    with _connect(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"])


# ---------------------------------------------------------------------------
# Clustering — structural, never the title
# ---------------------------------------------------------------------------


def test_cluster_key_is_four_structural_facts():
    """agent | type | arc_kind | YYYY-MM. Absent type and arc_kind have
    placeholders; the title is not read at all."""
    row = {
        "agent": "arc",
        "type": "pattern",
        "arc_kind": "graph",
        "title": "run-summary-1",
        "updated_at": "2026-08-14 09:12:31",
    }
    assert memory.cluster_key(row) == "arc|pattern|graph|2026-08"
    renamed = {**row, "title": "something else entirely"}
    assert memory.cluster_key(renamed) == memory.cluster_key(row)
    bare = {"agent": "user", "updated_at": "2026-01-02 00:00:00"}
    assert memory.cluster_key(bare) == "user|untyped|-|2026-01"
    assert memory.row_period({"updated_at": None}) == "unknown"
    assert not hasattr(memory, "cluster_prefix"), "the refuted title rule must be gone"


def test_clusters_split_by_month(tmp_path, capsys):
    """One agent, one type, one arc_kind, two months -> two clusters. The
    refuted rule put every run summary of every month in one bucket."""
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    for n in (1, 2):
        _seed(db, title=f"run-summary-{n}", arc_kind="graph", arc_id=f"t-{n}", age_days=200)
    for n in (3, 4, 5):
        _seed(db, title=f"run-summary-{n}", arc_kind="graph", arc_id=f"t-{n}", age_days=100)
    older, newer = _period(db, 200), _period(db, 100)
    assert older != newer

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    assert [c["key"] for c in out["clusters"]] == [
        f"arc|pattern|graph|{older}",
        f"arc|pattern|graph|{newer}",
    ]
    by_key = {c["key"]: c for c in out["clusters"]}
    assert by_key[f"arc|pattern|graph|{older}"]["members"] == 2
    assert by_key[f"arc|pattern|graph|{older}"]["period"] == older
    assert by_key[f"arc|pattern|graph|{newer}"]["member_titles"] == [
        "run-summary-3",
        "run-summary-4",
        "run-summary-5",
    ]
    assert out["rows_deleted"] == 5 and out["rows_inserted"] == 2


def test_cluster_key_separates_type_and_arc_kind(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    for n in (1, 2):
        _seed(db, title=f"run-summary-{n}", arc_kind="graph")
        _seed(db, title=f"run-summary-{n}", arc_kind="research")
        _seed(db, title=f"note-{n}", mem_type="reference", arc_kind="graph")
    period = _period(db, 200)

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")
    assert [c["key"] for c in out["clusters"]] == [
        f"arc|pattern|graph|{period}",
        f"arc|pattern|research|{period}",
        f"arc|reference|graph|{period}",
    ]


def test_only_arc_rows_collapse_until_an_agent_is_opted_in(tmp_path, capsys):
    """Default candidates are framework-written rows. The operator's own rows
    join only when named, and naming one never drops `arc`."""
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    for n in (1, 2):
        _seed(db, title=f"run-summary-{n}", arc_kind="graph")
        _seed(db, title=f"note-{n}", agent="user", mem_type="project")

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")
    assert out["agents"] == ["arc"]
    assert out["rows_deleted"] == 2 and out["rows_inserted"] == 1
    assert {"note-1", "note-2"} <= _titles(db)

    opted = _synth(db, capsys, "--older-than-days", "30", "--no-backup", "--agent", "user")
    assert opted["agents"] == ["arc", "user"]
    assert opted["rows_deleted"] == 2 and opted["rows_inserted"] == 1
    assert not {"note-1", "note-2"} & _titles(db)
    assert _row(db, f"digest:user|project|-|{_period(db, 200)}:part-1")["agent"] == "user"


def test_identity_numbered_titles_are_never_merged_away(tmp_path, capsys):
    """The refuted rule stripped trailing digits, so ADR-006 and ADR-009 —
    whose trailing number IS their identity — became one cluster and one of
    them was erased. They are operator rows, so the default does not touch
    them at all; opted in, they collapse by month with every title intact."""
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    identities = ["ADR-006", "ADR-009", "ADR-010", "ADR-011", "incident-1234", "incident-5678"]
    for title in identities:
        _seed(db, title=title, body=f"{title} said something", agent="user", mem_type="project")

    default_run = _synth(db, capsys, "--older-than-days", "30", "--no-backup")
    assert default_run["clusters"] == [] and default_run["rows_deleted"] == 0
    assert set(identities) <= _titles(db)

    opted = _synth(db, capsys, "--older-than-days", "30", "--no-backup", "--agent", "user")
    assert opted["rows_inserted"] == 1
    body = _row(db, "digest:user|project|-|%")["body"]
    for title in identities:
        assert f"- {title}: {title} said something" in body


def test_a_singleton_cluster_is_left_alone_and_reported(tmp_path, capsys):
    """A digest of one row is bigger than the row it replaces — the refuted
    build rewrote 94% of its clusters that way and GREW the store."""
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed(db, title="run-summary-1", arc_kind="graph", age_days=200)
    _seed(db, title="run-summary-2", arc_kind="graph", age_days=100)
    _seed(db, title="run-summary-3", arc_kind="graph", age_days=100)

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    assert out["skipped_singletons"] == {"count": 1, "titles": ["run-summary-1"]}
    assert len(out["clusters"]) == 1 and out["clusters"][0]["members"] == 2
    assert out["rows_deleted"] == 2 and out["rows_inserted"] == 1
    assert "run-summary-1" in _titles(db)
    assert memory.MIN_CLUSTER_SIZE == 2


# ---------------------------------------------------------------------------
# The digest rows
# ---------------------------------------------------------------------------


def test_digest_row_shape(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")
    assert out["rows_inserted"] == 1

    digest = _row(db, "digest:%")
    assert digest["title"] == _graph_digest_title(db)
    assert digest["agent"] == "arc"
    assert digest["type"] == "pattern"  # inside the CHECK constraint, no new type
    assert digest["arc_kind"] == "graph"
    assert digest["arc_id"] is None
    assert memory.has_tag(digest["tags"], "digest")
    assert memory.has_tag(digest["tags"], "terminal")  # union of member tags
    assert digest["body"].startswith("3 memories collapsed on ")
    assert "\nrange: " in digest["body"]
    for n in (1, 2, 3):
        assert f"- run-summary-{n} [t-{n}]: attempt {n} said something" in digest["body"]
    assert "second line" not in digest["body"]  # first non-empty body line only


def test_parts_carry_every_member_and_never_exceed_the_cap(tmp_path, capsys):
    """300 fat rows in one cluster. Every member — title AND arc_id — appears
    in exactly one part, no part passes 4,000 chars, and nothing is summarized
    away with an "…and N more" tail."""
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_many(
        db,
        [
            {
                "title": f"run-summary-{n}",
                "body": "fat " * 400,
                "arc_kind": "graph",
                "arc_id": f"task-{n}",
                "age_days": 100,
            }
            for n in range(300)
        ],
    )

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    assert len(out["clusters"]) == 1
    cluster = out["clusters"][0]
    assert cluster["members"] == 300 and cluster["parts"] > 1
    assert out["rows_deleted"] == 300 and out["rows_inserted"] == cluster["parts"]

    bodies = _digest_bodies(db)
    assert set(bodies) == {
        f"digest:{cluster['key']}:part-{k}" for k in range(1, cluster["parts"] + 1)
    }
    assert memory.DIGEST_BODY_MAX_CHARS == 4000
    for title, body in bodies.items():
        assert len(body) <= 4000, f"{title} is {len(body)} chars"
    joined = "".join(bodies.values())
    assert "…and " not in joined
    for n in range(300):
        assert joined.count(f"- run-summary-{n} [task-{n}]:") == 1
    # the per-part headers account for all 300, so nothing landed twice either
    counted = sum(int(m) for m in re.findall(r"^(\d+) memories collapsed on ", joined, re.M))
    assert counted == 300


def test_member_titles_are_findable_only_through_the_digest(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed(db, title="run-summary-1", body="obsoletetoken lives here", arc_kind="graph")
    _seed(db, title="run-summary-2", body="another obsoletetoken line", arc_kind="graph")

    _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    with _connect(db) as conn:
        hits = [
            dict(r)
            for r in conn.execute(
                """SELECT m.title, m.tags FROM memories_fts
                   JOIN memories m ON m.id = memories_fts.rowid
                   WHERE memories_fts MATCH ?""",
                ("obsoletetoken",),
            )
        ]
        by_title = [
            dict(r)
            for r in conn.execute(
                """SELECT m.title FROM memories_fts
                   JOIN memories m ON m.id = memories_fts.rowid
                   WHERE memories_fts MATCH ?""",
                ('"run-summary-1"',),
            )
        ]
    assert len(hits) == 1, "the collapsed members are gone but the digest must be findable"
    assert hits[0]["title"].startswith("digest:")
    assert memory.has_tag(hits[0]["tags"], "digest")
    # searching a member's own title now lands on the digest that swallowed it
    assert [r["title"] for r in by_title] == [_graph_digest_title(db)]


# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------


def test_feedback_user_and_decision_rows_are_untouched(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)
    # an ARC row tagged decision: exempt on the tag alone, not on its agent
    _seed(db, title="the panel settled it", tags="decision", arc_kind="graph")
    _seed(db, title="an older digest", tags="digest", arc_kind="graph", age_days=900)

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    survivors = _titles(db)
    assert "old feedback" in survivors
    assert "old user note" in survivors
    assert "the storage call" in survivors  # tagged `decision`
    assert "the panel settled it" in survivors  # tagged `decision`, agent arc
    assert "an older digest" in survivors  # tagged `digest`, the dedup rule
    assert not {"run-summary-1", "run-summary-2", "run-summary-3"} & survivors
    assert out["protected_types"] == ["feedback", "user"]


def test_second_run_is_idempotent(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)

    first = _synth(db, capsys, "--older-than-days", "30", "--no-backup")
    after_first = _titles(db)
    second = _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    assert first["rows_deleted"] == 3 and first["rows_inserted"] == 1
    assert second["rows_deleted"] == 0 and second["rows_inserted"] == 0
    assert second["clusters"] == []
    assert second["skipped_singletons"] == {"count": 0, "titles": []}
    assert second["backup_path"] is None  # nothing to collapse, nothing written
    assert _titles(db) == after_first


def test_type_narrows_and_never_widens(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)
    for n in (1, 2):
        _seed(db, title=f"lookup-{n}", mem_type="reference", arc_kind="graph")

    out = _synth(db, capsys, "--older-than-days", "30", "--type", "reference", "--no-backup")
    assert out["type"] == "reference"
    assert out["rows_deleted"] == 2
    assert "run-summary-1" in _titles(db)  # a different type, so untouched

    for protected in ("feedback", "user"):
        with pytest.raises(SystemExit) as exc:
            memory_main(
                ["--db", str(db), "synthesize", "--older-than-days", "30", "--type", protected]
            )
        assert exc.value.code == 1
        assert "never synthesized" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Write path: dry run, snapshot, rollback, VACUUM
# ---------------------------------------------------------------------------


def test_dry_run_does_not_touch_a_single_byte(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)
    before_digest = _sha256(db)
    before_titles = _titles(db)

    out = _synth(db, capsys, "--older-than-days", "30", "--dry-run")

    assert out["dry_run"] is True
    assert out["rows_deleted"] == 3 and out["rows_inserted"] == 1  # the PLAN
    assert out["bytes_before"] == db.stat().st_size
    assert out["bytes_after"] is None and out["backup_path"] is None
    assert "bytes_after_estimate" not in out, "the estimate pointed the wrong way; it is gone"
    assert _sha256(db) == before_digest
    assert _titles(db) == before_titles
    assert list(tmp_path.glob("*.bak")) == []


def test_dry_run_does_not_touch_a_legacy_schema_database(tmp_path, capsys):
    """No arc columns, no FTS table, no triggers. The old build ran
    executescript(SCHEMA_SQL) + FTS_SYNC_SQL + ALTER TABLE before it read, so
    a plain --dry-run silently migrated the operator's store."""
    db = _legacy_db(tmp_path / "legacy.db")
    before_digest = _sha256(db)

    out = _synth(db, capsys, "--older-than-days", "30", "--dry-run")

    assert _sha256(db) == before_digest, "--dry-run wrote to a legacy-schema store"
    assert list(tmp_path.iterdir()) == [db], "no journal, no side files"
    assert out["rows_deleted"] == 3 and out["rows_inserted"] == 1
    # arc_kind read as NULL through the missing column
    assert out["clusters"][0]["key"] == f"arc|pattern|-|{_period(db, 200)}"


def test_the_snapshot_precedes_the_write_and_never_overwrites(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    for n in (1, 2):
        _seed(db, title=f"run-summary-{n}", arc_kind="graph", age_days=200)

    first = _synth(db, capsys, "--older-than-days", "30")

    snapshot = Path(first["backup_path"])
    assert snapshot.parent == tmp_path
    assert re.fullmatch(r"m\.db\.\d{8}-\d{6}(-\d+)?\.bak", snapshot.name), snapshot.name
    assert _titles(snapshot) == {"run-summary-1", "run-summary-2"}, (
        "snapshot must predate the write"
    )
    assert "run-summary-1" not in _titles(db)

    for n in (3, 4):
        _seed(db, title=f"run-summary-{n}", arc_kind="graph", age_days=400)
    second = _synth(db, capsys, "--older-than-days", "30")

    assert second["backup_path"] != first["backup_path"]
    assert snapshot.exists(), "an earlier snapshot is never overwritten"
    assert len(list(tmp_path.glob("m.db.*.bak"))) == 2


def test_no_backup_leaves_no_snapshot(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    assert out["backup_path"] is None
    assert list(tmp_path.glob("*.bak")) == []


def test_a_failed_write_rolls_back_and_frames_the_error(tmp_path, capsys):
    """A BEFORE INSERT trigger that ABORTs on the SECOND digest title: the
    first digest is already inserted when it fires, so the store is only
    unchanged if the whole transaction rolls back. The old build let the raw
    sqlite3 traceback out instead."""
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    for n in (1, 2):
        _seed(db, title=f"run-summary-{n}", arc_kind="graph", age_days=200)
    for n in (3, 4):
        _seed(db, title=f"run-summary-{n}", arc_kind="graph", age_days=400)

    plan = _synth(db, capsys, "--older-than-days", "30", "--dry-run")
    assert len(plan["clusters"]) == 2
    doomed_title = f"digest:{plan['clusters'][1]['key']}:part-1"
    with _connect(db) as conn:
        conn.execute(
            f"""CREATE TRIGGER refuse_second BEFORE INSERT ON memories
                WHEN new.title = '{doomed_title}'
                BEGIN SELECT RAISE(ABORT, 'disk is on fire'); END"""
        )
        conn.commit()
    before_titles = _titles(db)
    before_rows = _row_count(db)

    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "synthesize", "--older-than-days", "30"])

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "synthesize failed and rolled back" in err
    assert "snapshot intact at" in err and "disk is on fire" in err
    assert _titles(db) == before_titles and _row_count(db) == before_rows
    (snapshot,) = list(tmp_path.glob("m.db.*.bak"))
    assert "run-summary-1" in _titles(snapshot)


def test_vacuum_reclaims_space_after_a_bulk_collapse(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_many(
        db,
        [
            {"title": f"run-summary-{n}", "body": "fat" * 700, "arc_kind": "graph"}
            for n in range(300)
        ],
    )

    before = db.stat().st_size
    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    assert out["rows_deleted"] == 300
    assert out["bytes_before"] == before
    assert out["bytes_after"] < before, "VACUUM did not hand the pages back"
    assert db.stat().st_size == out["bytes_after"]


def test_absent_db_is_refused(tmp_path, capsys):
    db = tmp_path / "nope" / "m.db"
    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "synthesize", "--older-than-days", "30"])
    assert exc.value.code == 1
    assert "no memory DB yet at" in capsys.readouterr().err
    assert not db.exists()


def test_bad_arguments_are_refused(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "synthesize", "--older-than-days", "0"])
    assert exc.value.code == 1
    assert "--older-than-days must be >= 1" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "synthesize", "--older-than-days", "30", "--type", "bogus"])
    assert exc.value.code == 1
    assert "invalid --type" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_structure_after_the_operation(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)
    digest_title = _graph_digest_title(db)
    target = tmp_path / "digest.md"

    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup", "--export", str(target))

    assert out["export_path"] == str(target)
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Memory digest — ")
    assert "current state" in text
    # Decisions, then digests, then everything else grouped by type.
    order = [text.index(h) for h in ("## Decisions", "## Digests", "## Everything else")]
    assert order == sorted(order)
    assert text.index("the storage call") < text.index(digest_title)
    assert text.index(digest_title) < text.index("## Everything else")
    assert "### feedback (1)" in text and "### user (1)" in text
    assert "run-summary-1" not in text  # collapsed, so gone from the store


def test_export_under_dry_run_describes_the_planned_state(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)
    digest_title = _graph_digest_title(db)
    target = tmp_path / "planned.md"

    _synth(db, capsys, "--older-than-days", "30", "--dry-run", "--export", str(target))

    text = target.read_text(encoding="utf-8")
    assert "planned state (dry run)" in text
    assert digest_title in text  # the digest that WOULD exist
    assert "run-summary-1" not in text  # the member that WOULD be gone
    assert "run-summary-1" in _titles(db)  # ...but the database is untouched


def test_export_default_path_lives_in_the_gitignored_wip_zone(tmp_path, capsys, monkeypatch):
    """`--export` with no value writes wip/memory-digest-<date>.md under the
    repo root, and `wip/` is gitignored — an export is never a tracked file."""
    ignore = (memory.PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "wip/" in [line.strip() for line in ignore]

    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    assert (
        memory.default_export_path("2026-09-03") == tmp_path / "wip" / "memory-digest-2026-09-03.md"
    )

    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)
    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup", "--export")

    written = Path(out["export_path"])
    assert written.parent == tmp_path / "wip"
    assert written.name.startswith("memory-digest-") and written.suffix == ".md"
    assert written.read_text(encoding="utf-8").startswith("# Memory digest — ")


# ---------------------------------------------------------------------------
# Decision-first ranking
# ---------------------------------------------------------------------------


def test_decision_tag_matching_is_whole_word():
    assert memory.has_tag("decision", "decision")
    assert memory.has_tag("terminal, decision", "decision")
    assert not memory.has_tag("decisions", "decision")
    assert not memory.has_tag("arc-decision", "decision")
    assert not memory.has_tag(None, "decision")


def test_decision_first_is_stable():
    rows = [
        {"title": "a", "tags": None},
        {"title": "b", "tags": "decision"},
        {"title": "c", "tags": "x"},
        {"title": "d", "tags": "y,decision"},
    ]
    assert [r["title"] for r in memory.decision_first(rows)] == ["b", "d", "a", "c"]


def test_search_general_ranks_decisions_first(tmp_path):
    db = tmp_path / "m.db"
    _init_db(db)
    for n in range(4):
        _seed(
            db,
            title=f"telemetry {n}",
            body="sqlite sqlite sqlite backend",
            agent="user",
            mem_type="pattern",
            age_days=1,
        )
    _seed(
        db,
        title="the storage call",
        body="sqlite backend chosen once",
        agent="user",
        mem_type="project",
        tags="decision",
        age_days=1,
    )

    rows = memory.search_general(query=memory.fts_or_query("sqlite backend"), limit=3, db_path=db)
    assert rows[0]["title"] == "the storage call", [r["title"] for r in rows]
    assert len(rows) == 3


def test_search_cli_ranks_decisions_first(tmp_path, capsys):
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    for n in range(4):
        _seed(db, title=f"telemetry {n}", body="sqlite sqlite sqlite", age_days=1)
    _seed(db, title="the storage call", body="sqlite once", tags="decision", age_days=1)

    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "search", "sqlite", "--limit", "3"])
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 3
    assert out["rows"][0]["title"] == "the storage call"


def test_priors_block_puts_decisions_first(_isolated_memory_db):
    """The planner's 1,500-char block is truncated from the end, so ordering
    is what decides whether a decision reaches the prompt at all."""
    db = _isolated_memory_db
    _seed(
        db,
        title="telemetry note",
        body="sqlite backend telemetry",
        agent="user",
        mem_type="pattern",
        age_days=1,
    )
    _seed(
        db,
        title="the storage call",
        body="sqlite backend chosen",
        agent="user",
        mem_type="project",
        tags="decision",
        age_days=1,
    )
    _seed(db, title="arc telemetry", body="a run said things", arc_kind="graph", age_days=2)
    _seed(
        db,
        title="arc decision",
        body="the panel settled it",
        tags="decision",
        arc_kind="graph",
        age_days=1,
    )

    seed = _common.priors_block("graph", "sqlite backend")

    assert seed.block
    assert seed.block.index("the storage call") < seed.block.index("telemetry note")
    assert seed.block.index("arc decision") < seed.block.index("arc telemetry")


# ---------------------------------------------------------------------------
# CLI drill — the argparse surface itself
# ---------------------------------------------------------------------------


def test_synthesize_cli_drill(tmp_path, capsys):
    """The subcommand exists on the shared parser, takes the documented flags,
    and emits the documented JSON contract."""
    parser = memory.build_parser()
    args = parser.parse_args(
        [
            "--db",
            "x.db",
            "synthesize",
            "--older-than-days",
            "30",
            "--dry-run",
            "--export",
            "--agent",
            "user",
            "--agent",
            "planner",
        ]
    )
    assert args.func is memory.cmd_synthesize
    assert args.older_than_days == 30 and args.dry_run is True
    assert args.export == "" and args.no_backup is False and args.type is None
    assert args.agent == ["user", "planner"]

    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed_mixed(db)
    _seed(db, title="alone in its month", arc_kind="graph", age_days=900)
    out = _synth(db, capsys, "--older-than-days", "30", "--no-backup")

    assert out["ok"] is True and out["action"] == "synthesize"
    assert set(out) == {
        "ok",
        "action",
        "db",
        "dry_run",
        "older_than_days",
        "agents",
        "type",
        "cutoff",
        "protected_types",
        "clusters",
        "skipped_singletons",
        "rows_deleted",
        "rows_inserted",
        "bytes_before",
        "bytes_after",
        "backup_path",
        "export_path",
    }
    assert out["agents"] == ["arc"]
    assert out["protected_types"] == ["feedback", "user"]
    assert set(out["clusters"][0]) == {"key", "period", "members", "parts", "member_titles"}
    assert set(out["skipped_singletons"]) == {"count", "titles"}
    assert out["skipped_singletons"]["titles"] == ["alone in its month"]


def test_backup_cli_snapshots_the_store(tmp_path, capsys):
    """`backup` is synthesize's snapshot with a CLI face — one `snapshot_db`."""
    db = _init_db(tmp_path / "m.db")
    capsys.readouterr()
    _seed(db, title="keep me", body="a durable line")
    dest = tmp_path / "copy.db"

    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(db), "backup", "--dest", str(dest)])
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)

    assert out == {
        "ok": True,
        "action": "backup",
        "source": str(db),
        "dest": str(dest),
        "bytes": dest.stat().st_size,
    }
    assert "keep me" in _titles(dest)

    with pytest.raises(SystemExit) as exc:
        memory_main(["--db", str(tmp_path / "gone.db"), "backup", "--dest", str(tmp_path / "x.db")])
    assert exc.value.code == 1
    assert "source database does not exist" in capsys.readouterr().err
    assert not (tmp_path / "x.db").exists()

    assert memory.build_parser().parse_args(["backup"]).dest == str(memory.DEFAULT_BACKUP_PATH)
