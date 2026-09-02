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
                WHERE {' AND '.join(clauses)}
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
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
            """
            params.append(limit)

        rows = conn.execute(sql, params).fetchall()

    return [_row_to_dict(r) for r in rows]


def summarize_run(
    *,
    arc_kind: str,
    arc_id: str,
    summary: str,
    tags: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Convenience for the common case: one end-of-run summary per arc.

    Uses the fixed title ``"run-summary"`` so re-running the same arc_id
    overwrites instead of appending. For multi-summary cases (e.g. one per
    phase), call ``arc_write`` directly with phase-suffixed titles.
    """
    return arc_write(
        arc_kind=arc_kind,
        arc_id=arc_id,
        title="run-summary",
        body=summary,
        tags=tags,
        db_path=db_path,
    )
