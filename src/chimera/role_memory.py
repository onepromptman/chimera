"""Role memory — layer 3 of the chimera memory system (sleeping, opt-in).

Mirrors :mod:`chimera.arc_memory` exactly: a universal adapter keyed on
``role`` + ``role_id`` instead of ``arc_kind`` + ``arc_id``. Same FTS5
store, same write/search shape, same dedup-on-write contract.

Status: **SLEEPING INFRASTRUCTURE.** This module is fully working but
intentionally unwired — no callable in :mod:`chimera.agents` uses it.
The chimera v6 design made agents stateless callables on purpose
(persona-drift was the v5 problem; ``agents.py`` was the fix).
Giving roles durable memory across calls re-opens that door — opt-in
per role only, and only when the cost is clearly justified for that
role. Opt-in per role; see below for the pattern and the
persona-drift caveat.

Typical use, *if* you decide a role should remember:

    # at the end of a contrarian-critic call
    role_memory.role_write(
        role="contrarian-critic",
        role_id="instance-or-arc-id",
        title="refute-tactic",
        body="grounding the refutation in the schema beat prose refutals 3:1",
    )

    # before the next contrarian call
    priors = role_memory.role_search(role="contrarian-critic", limit=3)

Storage shape:
    agent     = "role" (sentinel; keeps the existing NOT NULL constraint)
    type      = "pattern" (already in the type CHECK constraint)
    role      = caller-supplied role name (any string, lowercase/dash)
    role_id   = caller-supplied scope id (instance, arc id, or "global")
    title     = short label; the dedup key together with role + role_id
    body      = the learning text
    tags      = optional free-text tag string (FTS-searchable)

Implementation invariants kept identical to :mod:`chimera.arc_memory` so
a future integrator only has to learn one shape:
- No git or shell-process or wrapper-checkpoint calls — safe to import
  from anywhere (the grep guard in
  :mod:`tests.test_no_write_outside_wrapper` only scans
  ``src/chimera/arcs/`` and this module lives outside that tree)
- All search results are restricted to ``agent = "role"`` rows — user/
  arc memories never leak in
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
    _ensure_role_columns,
    _row_to_dict,
)

ROLE_AGENT_SENTINEL = "role"
ROLE_TYPE = "pattern"


def _validate_role_id(role: str, role_id: str) -> None:
    if not role or not role.strip():
        raise ValueError("role must be a non-empty string")
    if not role_id or not role_id.strip():
        raise ValueError("role_id must be a non-empty string")
    if any(ch.isspace() for ch in role):
        raise ValueError(f"role must not contain whitespace: {role!r}")
    if any(ch.isspace() for ch in role_id):
        raise ValueError(f"role_id must not contain whitespace: {role_id!r}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.executescript(FTS_SYNC_SQL)
    _ensure_arc_columns(conn)
    _ensure_role_columns(conn)


def role_write(
    *,
    role: str,
    role_id: str,
    title: str,
    body: str,
    tags: str | None = None,
    source_file: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Insert or update one role-memory row.

    Dedup key is (role, role_id, title). Second write with same key
    updates body/tags in place and bumps updated_at — it does not append.

    Returns the resulting row as a plain dict.
    """
    _validate_role_id(role, role_id)
    if not title or not title.strip():
        raise ValueError("title must be a non-empty string")
    if body is None:
        raise ValueError("body must not be None (pass an empty string if intentional)")

    target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH

    with _connect(target) as conn:
        _ensure_schema(conn)
        existing = conn.execute(
            """SELECT id FROM memories
               WHERE role = ? AND role_id = ? AND title = ?""",
            (role, role_id, title),
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
                   (agent, type, title, body, tags, source_file, role, role_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ROLE_AGENT_SENTINEL,
                    ROLE_TYPE,
                    title,
                    body,
                    tags,
                    source_file,
                    role,
                    role_id,
                ),
            )
            row_id = cursor.lastrowid

        conn.commit()
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (row_id,)).fetchone()

    return _row_to_dict(row)


def role_search(
    *,
    query: str | None = None,
    role: str | None = None,
    role_id: str | None = None,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[dict]:
    """Search role memories. Filters compose with AND.

    - ``query`` (FTS5 match) is optional. Present → bm25 ordered (best
      first). Absent → updated_at DESC.
    - ``role`` / ``role_id`` are exact-match filters.
    - Results are restricted to role rows (agent = ROLE_AGENT_SENTINEL) so
      this never returns user, arc, or other memories.
    """
    target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH

    with _connect(target) as conn:
        _ensure_schema(conn)
        params: list[object] = []
        if query is not None and query.strip():
            clauses = ["memories_fts MATCH ?", "m.agent = ?"]
            params.extend([query, ROLE_AGENT_SENTINEL])
            if role is not None:
                clauses.append("m.role = ?")
                params.append(role)
            if role_id is not None:
                clauses.append("m.role_id = ?")
                params.append(role_id)
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
            params.append(ROLE_AGENT_SENTINEL)
            if role is not None:
                clauses.append("role = ?")
                params.append(role)
            if role_id is not None:
                clauses.append("role_id = ?")
                params.append(role_id)
            sql = f"""
                SELECT * FROM memories
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
            """
            params.append(limit)

        rows = conn.execute(sql, params).fetchall()

    return [_row_to_dict(r) for r in rows]


def summarize_role(
    *,
    role: str,
    role_id: str,
    summary: str,
    tags: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Convenience for one summary per (role, role_id).

    Uses the fixed title ``"role-summary"`` so re-running the same scope
    overwrites instead of appending. For multi-summary cases, call
    :func:`role_write` directly with distinct titles.
    """
    return role_write(
        role=role,
        role_id=role_id,
        title="role-summary",
        body=summary,
        tags=tags,
        db_path=db_path,
    )
