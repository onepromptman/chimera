"""Arc memory — layer 2 of the chimera memory system.

A universal adapter: takes any ``arc_kind`` string + ``arc_id`` and stores a
title/body row in the same FTS5-backed SQLite store as user/feedback/project
memories (``.claude/memory.db``). No arc kind is hardcoded — research, design,
and any future arc share the same write/search functions.

The intent is for an arc to call ``arc_write(...)`` at the end of a run (or
between phases) with what it learned: what worked, what critics refuted, where
the verify gate failed, which payloads hit null-degrade. The next instance of
the same arc kind calls ``arc_search(arc_kind=...)`` before its first agent
call to pull that learning forward (search-before-draft, the existing chimera
law, applied at the arc layer).

Storage shape:
    agent     = "arc"  (sentinel; keeps the existing NOT NULL constraint)
    type      = "pattern"  (already in the type CHECK constraint)
    arc_kind  = caller-supplied kind string (any value, lowercase/dash)
    arc_id    = caller-supplied run id (the arc instance)
    title     = short label; the dedup key together with arc_kind+arc_id
    body      = the learning text
    tags      = optional free-text tag string (FTS-searchable)

Terminal run summaries go through ``append_run_summary`` (title
``run-summary-<n>``): the dedup key means a FIXED title turns a second
attempt on the same arc_id into an UPDATE, which erased what the first
attempt learned (audit 2026-09-02, P1-6). The fixed-title convenience that
did exactly that was deleted 2026-09-03; nothing but its own test called it.

This module must NOT call into git, subprocess, or runner.checkpoint() so
that arcs can import it freely (see tests/test_no_write_outside_wrapper.py).
It lives at src/chimera/arc_memory.py, NOT under src/chimera/arcs/, so the
grep guard in that test does not apply here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chimera.memory import (
    DEFAULT_DB_PATH,
    FTS_SYNC_SQL,
    SCHEMA_SQL,
    _connect,
    _ensure_arc_columns,
    _row_to_dict,
)

ARC_AGENT_SENTINEL = "arc"
ARC_TYPE = "pattern"


def _validate_kind_id(arc_kind: str, arc_id: str) -> None:
    if not arc_kind or not arc_kind.strip():
        raise ValueError("arc_kind must be a non-empty string")
    if not arc_id or not arc_id.strip():
        raise ValueError("arc_id must be a non-empty string")
    if any(ch.isspace() for ch in arc_kind):
        raise ValueError(f"arc_kind must not contain whitespace: {arc_kind!r}")
    if any(ch.isspace() for ch in arc_id):
        raise ValueError(f"arc_id must not contain whitespace: {arc_id!r}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.executescript(FTS_SYNC_SQL)
    _ensure_arc_columns(conn)


def arc_write(
    *,
    arc_kind: str,
    arc_id: str,
    title: str,
    body: str,
    tags: str | None = None,
    source_file: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Insert or update one arc-memory row.

    Dedup key is (arc_kind, arc_id, title). Calling twice with the same key
    updates body/tags in place and bumps updated_at — it does not append a
    new row.

    Returns the resulting row as a plain dict.
    """
    _validate_kind_id(arc_kind, arc_id)
    if not title or not title.strip():
        raise ValueError("title must be a non-empty string")
    if body is None:
        raise ValueError("body must not be None (pass an empty string if intentional)")

    target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH

    with _connect(target) as conn:
        _ensure_schema(conn)
        existing = conn.execute(
            """SELECT id FROM memories
               WHERE arc_kind = ? AND arc_id = ? AND title = ?""",
            (arc_kind, arc_id, title),
        ).fetchone()

        if existing is not None:
            conn.execute(
                """UPDATE memories
                   SET body = ?, tags = ?, source_file = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (body, tags, source_file, existing["id"]),
            )
            row_id = existing["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO memories
                   (agent, type, title, body, tags, source_file, arc_kind, arc_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ARC_AGENT_SENTINEL,
                    ARC_TYPE,
                    title,
                    body,
                    tags,
                    source_file,
                    arc_kind,
                    arc_id,
                ),
            )
            row_id = cursor.lastrowid

        conn.commit()
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (row_id,)).fetchone()

    return _row_to_dict(row)


def arc_search(
    *,
    query: str | None = None,
    arc_kind: str | None = None,
    arc_id: str | None = None,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[dict]:
    """Search arc memories. Filters compose with AND.

    - ``query`` (FTS5 match) is optional. When present, results are ordered
      by bm25 score (best first). When absent, results are ordered by
      updated_at DESC.
    - ``arc_kind`` / ``arc_id`` are exact-match filters.
    - Results are restricted to arc-memory rows (agent = ARC_AGENT_SENTINEL)
      so this never returns user/feedback/project memories — those go
      through the existing ``search``/``get`` CLI surface.
    """
    target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH

    with _connect(target) as conn:
        _ensure_schema(conn)
        params: list[object] = []
        if query is not None and query.strip():
            clauses = ["memories_fts MATCH ?", "m.agent = ?"]
            params.extend([query, ARC_AGENT_SENTINEL])
            if arc_kind is not None:
                clauses.append("m.arc_kind = ?")
                params.append(arc_kind)
            if arc_id is not None:
                clauses.append("m.arc_id = ?")
                params.append(arc_id)
            sql = f"""
                SELECT m.*, bm25(memories_fts) AS score
                FROM memories_fts
                JOIN memories m ON m.id = memories_fts.rowid
                WHERE {" AND ".join(clauses)}
                ORDER BY score
                LIMIT ?
            """
            params.append(limit)
        else:
            clauses = ["agent = ?"]
            params.append(ARC_AGENT_SENTINEL)
            if arc_kind is not None:
                clauses.append("arc_kind = ?")
                params.append(arc_kind)
            if arc_id is not None:
                clauses.append("arc_id = ?")
                params.append(arc_id)
            sql = f"""
                SELECT * FROM memories
                WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
            """
            params.append(limit)

        rows = conn.execute(sql, params).fetchall()

    return [_row_to_dict(r) for r in rows]


RUN_SUMMARY_PREFIX = "run-summary"


def run_summary_count(*, arc_kind: str, arc_id: str, db_path: Path | None = None) -> int:
    """How many ``run-summary*`` rows already exist for this (kind, id)."""
    _validate_kind_id(arc_kind, arc_id)
    target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    with _connect(target) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM memories
               WHERE arc_kind = ? AND arc_id = ? AND title LIKE ?""",
            (arc_kind, arc_id, f"{RUN_SUMMARY_PREFIX}%"),
        ).fetchone()
    return int(row["n"]) if row is not None else 0


def append_run_summary(
    *,
    arc_kind: str,
    arc_id: str,
    summary: str,
    tags: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Append one attempt's end-of-run summary — never clobber a prior one.

    The title is ``run-summary-<n>`` where n is 1 + the number of existing
    ``run-summary*`` rows for this (arc_kind, arc_id), so a PASS and a later
    FAIL (or a ``chimera retry`` re-run) on the same task id each keep their
    own row. The dedup key of ``arc_write`` still holds inside one attempt:
    a second call in the SAME attempt is a new row only because the count
    moved, which is what "attempt" means here.
    """
    attempt = run_summary_count(arc_kind=arc_kind, arc_id=arc_id, db_path=db_path) + 1
    return arc_write(
        arc_kind=arc_kind,
        arc_id=arc_id,
        title=f"{RUN_SUMMARY_PREFIX}-{attempt}",
        body=summary,
        tags=tags,
        db_path=db_path,
    )
