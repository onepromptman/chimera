#!/usr/bin/env python3
"""SQLite memory backend CLI for the chimera framework.

A thin Python CLI over a single SQLite database (default ``~/.chimera/memory.db``),
accessed via shell calls from agents. No third-party dependencies; stdlib only.

Subcommands:
    init           Create the database, schema, FTS5 virtual table, and indexes.
    set            Insert or update a memory row.
    get            Read memory rows (filter by agent / type / id).
    search         FTS5 full-text search across title, body, tags.
    list           Index view of memories (id, agent, type, title, updated_at).
    migrate        Parse .claude/agent-memory/*/MEMORY.md and ingest into SQLite.
    migrate-db     Move a legacy <repo>/.claude/memory.db to the new home.
    backup         Snapshot the database to ``<db>.bak`` next to the db file.

Default path resolution (see ``_resolve_default_db_path``):
    1. ``$CHIMERA_DB_PATH`` if set.
    2. ``~/.chimera/memory.db`` — per-machine, survives branch switches,
       gets real 0600 on Linux/macOS (WSL drvfs is a known no-op).
    3. Falls back to ``<repo>/.claude/memory.db`` ONLY if that file exists
       and the home-dir target does not — so existing checkouts keep
       working until the user runs ``migrate-db``.

All commands emit JSON on stdout; errors go to stderr with non-zero exit codes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _detect_project_root() -> Path:
    """Repo root: $CHIMERA_ROOT if set, else nearest ancestor with .git."""
    env = os.environ.get("CHIMERA_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


PROJECT_ROOT = _detect_project_root()
LEGACY_DB_PATH = PROJECT_ROOT / ".claude" / "memory.db"
AGENT_MEMORY_ROOT = PROJECT_ROOT / ".claude" / "agent-memory"


def _resolve_default_db_path() -> Path:
    """Return the default SQLite path per the rules in the module docstring.

    Resolution order:
      1. $CHIMERA_DB_PATH if set (absolute or repo-relative).
      2. ~/.chimera/memory.db — the new per-machine home.
      3. The legacy <repo>/.claude/memory.db, BUT only as a fallback when
         the new home does not exist and the legacy one does. This keeps
         pre-migration checkouts working; once the user runs ``migrate-db``
         (or the new home exists for any reason), the legacy path is
         ignored.
    """
    env = os.environ.get("CHIMERA_DB_PATH")
    if env:
        p = Path(env)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()
    new_home = Path.home() / ".chimera" / "memory.db"
    if new_home.exists():
        return new_home
    if LEGACY_DB_PATH.exists():
        return LEGACY_DB_PATH
    return new_home  # fresh install: create it at the new home


DEFAULT_DB_PATH = _resolve_default_db_path()
DEFAULT_BACKUP_PATH = DEFAULT_DB_PATH.with_suffix(DEFAULT_DB_PATH.suffix + ".bak")

VALID_TYPES = {"user", "feedback", "project", "reference", "pattern"}

# Schema is fixed by the memory-layer design.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    type TEXT CHECK(type IN ('user','feedback','project','reference','pattern')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT,
    source_file TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, body, tags, content=memories
);
CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent);
CREATE INDEX IF NOT EXISTS idx_memories_type  ON memories(type);
"""

# Layer-2/recency indexes. Created after the arc/role columns exist (they are
# added by _ensure_arc_columns / _ensure_role_columns), so they live outside
# SCHEMA_SQL and are applied by _ensure_scaling_indexes during init. Negligible
# at today's row counts; they keep arc-lookup (reflect) and freshness/recency
# queries index-backed as the store grows.
SCALING_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_memories_arc     ON memories(arc_kind, arc_id);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at);
"""

# Triggers keep the FTS index in sync with the base table. They are not in the
# ADR's literal schema block but are required for FTS5 contentless-style sync;
# without them search would return stale rows after set/migrate.
FTS_SYNC_SQL = """
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, body, tags)
    VALUES (new.id, new.title, new.body, COALESCE(new.tags, ''));
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, body, tags)
    VALUES ('delete', old.id, old.title, old.body, COALESCE(old.tags, ''));
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, body, tags)
    VALUES ('delete', old.id, old.title, old.body, COALESCE(old.tags, ''));
    INSERT INTO memories_fts(rowid, title, body, tags)
    VALUES (new.id, new.title, new.body, COALESCE(new.tags, ''));
END;
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with row factory and foreign keys on."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_arc_columns(conn: sqlite3.Connection) -> None:
    """Add arc_kind/arc_id columns to memories if missing.

    Layer-2 (arc memory) extension. SQLite has no ADD COLUMN IF NOT EXISTS,
    so we check PRAGMA table_info first. Columns are nullable so legacy
    user/feedback/project/reference rows continue to work unchanged.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
    if "arc_kind" not in cols:
        conn.execute("ALTER TABLE memories ADD COLUMN arc_kind TEXT")
    if "arc_id" not in cols:
        conn.execute("ALTER TABLE memories ADD COLUMN arc_id TEXT")


def _ensure_role_columns(conn: sqlite3.Connection) -> None:
    """Add role/role_id columns to memories if missing.

    Layer-3 (role/agent memory) extension. Parked-but-ready: the schema
    accepts role-tagged rows now; no agent in agents.py wires to it yet.
    Opt-in per role; see the role_memory module docstring for the pattern and the
    persona-drift caveat. Columns are nullable so layer-1/2 rows are
    unaffected.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
    if "role" not in cols:
        conn.execute("ALTER TABLE memories ADD COLUMN role TEXT")
    if "role_id" not in cols:
        conn.execute("ALTER TABLE memories ADD COLUMN role_id TEXT")


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict for JSON serialization."""
    return {k: row[k] for k in row.keys()}


def _emit(payload: object, exit_code: int = 0) -> None:
    """Write a JSON payload to stdout and exit with the given code."""
    json.dump(payload, sys.stdout, indent=2, default=str, sort_keys=True)
    sys.stdout.write("\n")
    sys.exit(exit_code)


def _die(message: str, exit_code: int = 1) -> None:
    """Write an error message to stderr and exit non-zero."""
    sys.stderr.write(f"error: {message}\n")
    sys.exit(exit_code)


def _infer_type_from_filename(filename: str) -> str:
    """Map a memory .md filename prefix to a memory type per the spec."""
    name = filename.lower()
    if name.startswith("feedback_") or name.startswith("feedback-"):
        return "feedback"
    if name.startswith("project_") or name.startswith("project-"):
        return "project"
    if name.startswith("reference_") or name.startswith("reference-"):
        return "reference"
    if name.startswith("user_") or name.startswith("user-"):
        return "user"
    return "pattern"


# Bullet line shape (em-dash or hyphen-dash separator):
#   - [Title](relative/path.md) — body hook
#   - [Title](relative/path.md) - body hook
_BULLET_RE = re.compile(
    r"^\s*-\s*\[(?P<title>[^\]]+)\]\((?P<src>[^)]+)\)\s*[—\-]+\s*(?P<body>.+?)\s*$"
)


def _parse_memory_md(md_path: Path) -> list[dict]:
    """Extract memory entries from a MEMORY.md index file.

    Each top-level bullet of the form
        - [Title](file.md) — one-line hook
    becomes one dict with title, source_file, body, type.
    """
    entries: list[dict] = []
    text = md_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        match = _BULLET_RE.match(line)
        if not match:
            continue
        title = match.group("title").strip()
        src = match.group("src").strip()
        body = match.group("body").strip()
        entries.append(
            {
                "title": title,
                "source_file": src,
                "body": body,
                "type": _infer_type_from_filename(src),
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Subcommand: init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> None:
    """Create the SQLite database, schema, FTS5 table, indexes, and triggers."""
    db_path = Path(args.db)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(FTS_SYNC_SQL)
        _ensure_arc_columns(conn)
        _ensure_role_columns(conn)
        conn.executescript(SCALING_INDEX_SQL)  # needs arc/role columns to exist first
        conn.commit()
    db_path.chmod(0o600)
    _emit({"ok": True, "db": str(db_path), "action": "init"})


# ---------------------------------------------------------------------------
# Subcommand: set
# ---------------------------------------------------------------------------


def cmd_set(args: argparse.Namespace) -> None:
    """Insert a new memory row, or update an existing row matched by agent+title."""
    if args.type not in VALID_TYPES:
        _die(f"invalid --type {args.type!r}; must be one of {sorted(VALID_TYPES)}")

    db_path = Path(args.db)
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM memories WHERE agent = ? AND title = ?",
            (args.agent, args.title),
        ).fetchone()

        if existing is not None:
            conn.execute(
                """UPDATE memories
                   SET type = ?, body = ?, tags = ?, source_file = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (args.type, args.body, args.tags, args.source_file, existing["id"]),
            )
            row_id = existing["id"]
            action = "update"
        else:
            cursor = conn.execute(
                """INSERT INTO memories (agent, type, title, body, tags, source_file)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    args.agent,
                    args.type,
                    args.title,
                    args.body,
                    args.tags,
                    args.source_file,
                ),
            )
            row_id = cursor.lastrowid
            action = "insert"

        conn.commit()
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (row_id,)).fetchone()

    _emit({"ok": True, "action": action, "row": _row_to_dict(row)})


# ---------------------------------------------------------------------------
# Subcommand: get
# ---------------------------------------------------------------------------


def cmd_get(args: argparse.Namespace) -> None:
    """Read memory rows filtered by agent/type/id."""
    db_path = Path(args.db)
    clauses: list[str] = []
    params: list[object] = []
    if args.id is not None:
        clauses.append("id = ?")
        params.append(args.id)
    if args.agent:
        clauses.append("agent = ?")
        params.append(args.agent)
    if args.type:
        if args.type not in VALID_TYPES:
            _die(f"invalid --type {args.type!r}")
        clauses.append("type = ?")
        params.append(args.type)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM memories {where} ORDER BY updated_at DESC"

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    _emit({"ok": True, "count": len(rows), "rows": [_row_to_dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# Subcommand: search
# ---------------------------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> None:
    """FTS5 search across title, body, and tags.

    Token-lean by default: returns a focused FTS ``snippet`` of the body
    (the matching window) instead of the full body, so search-before-draft
    pays for relevance signal, not the whole row. Pass ``--full`` to get the
    complete rows (``m.*``) when an agent genuinely needs the entire body;
    otherwise fetch one by id with ``get``.
    """
    db_path = Path(args.db)
    clauses = ["memories_fts MATCH ?"]
    params: list[object] = [args.query]
    if args.agent:
        clauses.append("m.agent = ?")
        params.append(args.agent)
    if args.type:
        if args.type not in VALID_TYPES:
            _die(f"invalid --type {args.type!r}")
        clauses.append("m.type = ?")
        params.append(args.type)

    if getattr(args, "full", False):
        projection = "m.*, bm25(memories_fts) AS score"
    else:
        # snippet(table, col_index=1 -> body, no markup, ' … ' ellipsis, 14 tokens)
        projection = (
            "m.id, m.agent, m.type, m.title, "
            "snippet(memories_fts, 1, '', '', ' … ', 14) AS snippet, "
            "m.tags, m.updated_at, m.arc_kind, m.arc_id, "
            "bm25(memories_fts) AS score"
        )

    sql = f"""
        SELECT {projection}
        FROM memories_fts
        JOIN memories m ON m.id = memories_fts.rowid
        WHERE {' AND '.join(clauses)}
        ORDER BY score
        LIMIT ?
    """
    params.append(args.limit)

    with _connect(db_path) as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            _die(f"FTS query failed: {exc}")

    _emit(
        {
            "ok": True,
            "query": args.query,
            "count": len(rows),
            "rows": [_row_to_dict(r) for r in rows],
        }
    )


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> None:
    """Compact index view: id, agent, type, title, updated_at."""
    db_path = Path(args.db)
    clauses: list[str] = []
    params: list[object] = []
    if args.agent:
        clauses.append("agent = ?")
        params.append(args.agent)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT id, agent, type, title, updated_at
        FROM memories {where}
        ORDER BY agent, updated_at DESC
    """
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    _emit({"ok": True, "count": len(rows), "rows": [_row_to_dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# Subcommand: migrate
# ---------------------------------------------------------------------------


def cmd_migrate(args: argparse.Namespace) -> None:
    """Ingest MEMORY.md indexes into SQLite.

    Two modes:
        per-agent (default): scan <memory_root>/<agent>/MEMORY.md.
        flat (--source DIR): read DIR/MEMORY.md and assign every entry to
            --agent (default 'user'). Used to sweep the harness auto-memory
            dir at ~/.claude/projects/.../memory/.

    Idempotent in both modes: skips rows where (agent, title, source_file)
    already exists AND was updated within the last 24 hours. Source .md
    files are read-only.
    """
    db_path = Path(args.db)

    flat_mode = args.source is not None
    if flat_mode:
        source_dir = Path(args.source).expanduser()
        if not source_dir.exists():
            _die(f"source dir not found: {source_dir}")
        if not (source_dir / "MEMORY.md").exists():
            _die(f"no MEMORY.md in source dir: {source_dir}")
        flat_agent = args.agent or "user"
        memory_md_paths = [source_dir / "MEMORY.md"]
    else:
        root = Path(args.memory_root)
        if not root.exists():
            _die(f"memory root not found: {root}")
        memory_md_paths = sorted(root.glob("*/MEMORY.md"))

    # Ensure schema exists in case migrate is called before init.
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(FTS_SYNC_SQL)
        _ensure_arc_columns(conn)
        _ensure_role_columns(conn)
        conn.executescript(SCALING_INDEX_SQL)  # needs arc/role columns to exist first
        conn.commit()

    threshold = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    ).strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    updated = 0
    skipped = 0
    files_seen = 0
    entries_seen = 0
    per_agent: dict[str, int] = {}

    with _connect(db_path) as conn:
        for memory_md in memory_md_paths:
            files_seen += 1
            agent = flat_agent if flat_mode else memory_md.parent.name
            entries = _parse_memory_md(memory_md)
            for entry in entries:
                entries_seen += 1
                source_file = entry["source_file"]
                title = entry["title"]
                body = entry["body"]
                mem_type = entry["type"]

                existing = conn.execute(
                    """SELECT id, updated_at FROM memories
                       WHERE agent = ? AND title = ? AND source_file = ?""",
                    (agent, title, source_file),
                ).fetchone()

                if existing is not None:
                    # Idempotency guard: only update if older than 24h.
                    if existing["updated_at"] >= threshold:
                        skipped += 1
                        continue
                    conn.execute(
                        """UPDATE memories
                           SET type = ?, body = ?, updated_at = datetime('now')
                           WHERE id = ?""",
                        (mem_type, body, existing["id"]),
                    )
                    updated += 1
                else:
                    conn.execute(
                        """INSERT INTO memories
                           (agent, type, title, body, tags, source_file)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (agent, mem_type, title, body, None, source_file),
                    )
                    inserted += 1
                per_agent[agent] = per_agent.get(agent, 0) + 1
        conn.commit()

    _emit(
        {
            "ok": True,
            "action": "migrate",
            "files_seen": files_seen,
            "entries_seen": entries_seen,
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "per_agent": per_agent,
        }
    )


# ---------------------------------------------------------------------------
# Subcommand: backup
# ---------------------------------------------------------------------------


def cmd_arc_write(args: argparse.Namespace) -> None:
    """CLI wrapper for arc_memory.arc_write."""
    from chimera.arc_memory import arc_write  # local import: avoid cycle at load

    try:
        row = arc_write(
            arc_kind=args.kind,
            arc_id=args.arc_id,
            title=args.title,
            body=args.body,
            tags=args.tags,
            source_file=args.source_file,
            db_path=Path(args.db),
        )
    except ValueError as exc:
        _die(str(exc))
    _emit({"ok": True, "action": "arc-write", "row": row})


def cmd_arc_search(args: argparse.Namespace) -> None:
    """CLI wrapper for arc_memory.arc_search."""
    from chimera.arc_memory import arc_search

    rows = arc_search(
        query=args.query,
        arc_kind=args.arc_kind,
        arc_id=args.arc_id,
        limit=args.limit,
        db_path=Path(args.db),
    )
    _emit(
        {
            "ok": True,
            "action": "arc-search",
            "query": args.query,
            "arc_kind": args.arc_kind,
            "arc_id": args.arc_id,
            "count": len(rows),
            "rows": rows,
        }
    )


def cmd_role_write(args: argparse.Namespace) -> None:
    """CLI wrapper for role_memory.role_write (layer-3, sleeping)."""
    from chimera.role_memory import role_write

    try:
        row = role_write(
            role=args.role,
            role_id=args.role_id,
            title=args.title,
            body=args.body,
            tags=args.tags,
            source_file=args.source_file,
            db_path=Path(args.db),
        )
    except ValueError as exc:
        _die(str(exc))
    _emit({"ok": True, "action": "role-write", "row": row})


def cmd_role_search(args: argparse.Namespace) -> None:
    """CLI wrapper for role_memory.role_search (layer-3, sleeping)."""
    from chimera.role_memory import role_search

    rows = role_search(
        query=args.query,
        role=args.role,
        role_id=args.role_id,
        limit=args.limit,
        db_path=Path(args.db),
    )
    _emit(
        {
            "ok": True,
            "action": "role-search",
            "query": args.query,
            "role": args.role,
            "role_id": args.role_id,
            "count": len(rows),
            "rows": rows,
        }
    )


def cmd_migrate_db(args: argparse.Namespace) -> None:
    """Move a legacy <repo>/.claude/memory.db to a new home (default ~/.chimera/).

    The new path is taken from --dest (defaults to ~/.chimera/memory.db).
    Behavior:
      - source exists, dest missing  -> copy bytes (preserves rowids), then
        rename source to source + ".migrated" so a re-run is a clean no-op.
      - source missing, dest exists  -> nothing to do (already migrated).
      - source missing, dest missing -> nothing to do (fresh install).
      - both exist                   -> refuse; --force to overwrite dest.
    """
    src = Path(args.src)
    dst = Path(args.dest)
    if not src.exists() and not dst.exists():
        _emit({"ok": True, "action": "migrate-db", "noop": "neither path exists"})
        return
    if not src.exists() and dst.exists():
        _emit({"ok": True, "action": "migrate-db", "noop": "already at dest", "dest": str(dst)})
        return
    if src.exists() and dst.exists() and not args.force:
        _die(f"both {src} and {dst} exist; pass --force to overwrite dest", exit_code=2)
    dst.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy2(src, dst)
    try:
        dst.chmod(0o600)
    except OSError:
        pass  # WSL drvfs no-op
    migrated_marker = src.with_suffix(src.suffix + ".migrated")
    if migrated_marker.exists():
        migrated_marker.unlink()
    src.rename(migrated_marker)
    _emit(
        {
            "ok": True,
            "action": "migrate-db",
            "src": str(src),
            "dest": str(dst),
            "marker": str(migrated_marker),
            "bytes": dst.stat().st_size,
        }
    )


def cmd_backup(args: argparse.Namespace) -> None:
    """Atomic SQLite backup using the online backup API."""
    src_path = Path(args.db)
    dst_path = Path(args.dest)
    if not src_path.exists():
        _die(f"source database does not exist: {src_path}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(src_path)) as src, sqlite3.connect(str(dst_path)) as dst:
        src.backup(dst)

    _emit(
        {
            "ok": True,
            "action": "backup",
            "source": str(src_path),
            "dest": str(dst_path),
            "bytes": dst_path.stat().st_size,
        }
    )


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser and subparsers."""
    parser = argparse.ArgumentParser(
        prog="chimera_memory",
        description="SQLite memory backend CLI for the chimera framework.",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"path to the SQLite database (default: {DEFAULT_DB_PATH})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create database + schema + FTS5 table").set_defaults(
        func=cmd_init
    )

    p_set = sub.add_parser("set", help="insert or update a memory row")
    p_set.add_argument("--agent", required=True)
    p_set.add_argument("--type", required=True)
    p_set.add_argument("--title", required=True)
    p_set.add_argument("--body", required=True)
    p_set.add_argument("--tags", default=None, help="comma-delimited tag string")
    p_set.add_argument("--source-file", dest="source_file", default=None)
    p_set.set_defaults(func=cmd_set)

    p_get = sub.add_parser("get", help="read memories")
    p_get.add_argument("--agent", default=None)
    p_get.add_argument("--type", default=None)
    p_get.add_argument("--id", type=int, default=None)
    p_get.set_defaults(func=cmd_get)

    p_search = sub.add_parser("search", help="FTS5 full-text search")
    p_search.add_argument("query")
    p_search.add_argument("--agent", default=None)
    p_search.add_argument("--type", default=None)
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument(
        "--full",
        action="store_true",
        help="return full rows (whole body) instead of token-lean snippets",
    )
    p_search.set_defaults(func=cmd_search)

    p_list = sub.add_parser("list", help="compact index view")
    p_list.add_argument("--agent", default=None)
    p_list.set_defaults(func=cmd_list)

    p_mig = sub.add_parser("migrate", help="ingest MEMORY.md indexes (per-agent or flat)")
    p_mig.add_argument(
        "--memory-root",
        dest="memory_root",
        default=str(AGENT_MEMORY_ROOT),
        help=f"per-agent mode: root containing <agent>/MEMORY.md (default: {AGENT_MEMORY_ROOT})",
    )
    p_mig.add_argument(
        "--source",
        default=None,
        help="flat mode: a single dir with a MEMORY.md (e.g. ~/.claude/projects/.../memory)",
    )
    p_mig.add_argument(
        "--agent",
        default=None,
        help="flat mode: agent name to assign every entry (default: user)",
    )
    p_mig.set_defaults(func=cmd_migrate)

    p_aw = sub.add_parser("arc-write", help="layer-2: write one arc-memory row")
    p_aw.add_argument("--kind", required=True, help="arc kind (e.g. research, design, or any string)")
    p_aw.add_argument("--arc-id", dest="arc_id", required=True, help="arc instance id")
    p_aw.add_argument("--title", required=True, help="short label; dedup key with kind+arc-id")
    p_aw.add_argument("--body", required=True, help="the learning text")
    p_aw.add_argument("--tags", default=None, help="optional comma-delimited tag string")
    p_aw.add_argument("--source-file", dest="source_file", default=None)
    p_aw.set_defaults(func=cmd_arc_write)

    p_as = sub.add_parser("arc-search", help="layer-2: search arc memories")
    p_as.add_argument("query", nargs="?", default=None, help="FTS5 query (optional)")
    p_as.add_argument("--arc-kind", dest="arc_kind", default=None, help="filter by arc kind")
    p_as.add_argument("--arc-id", dest="arc_id", default=None, help="filter by arc instance id")
    p_as.add_argument("--limit", type=int, default=20)
    p_as.set_defaults(func=cmd_arc_search)

    p_rw = sub.add_parser("role-write", help="layer-3 (sleeping): write one role-memory row")
    p_rw.add_argument("--role", required=True, help="role name (e.g. contrarian-critic, or any string)")
    p_rw.add_argument("--role-id", dest="role_id", required=True, help="scope id (instance, arc id, or 'global')")
    p_rw.add_argument("--title", required=True, help="short label; dedup key with role+role-id")
    p_rw.add_argument("--body", required=True, help="the learning text")
    p_rw.add_argument("--tags", default=None, help="optional comma-delimited tag string")
    p_rw.add_argument("--source-file", dest="source_file", default=None)
    p_rw.set_defaults(func=cmd_role_write)

    p_rs = sub.add_parser("role-search", help="layer-3 (sleeping): search role memories")
    p_rs.add_argument("query", nargs="?", default=None, help="FTS5 query (optional)")
    p_rs.add_argument("--role", default=None, help="filter by role")
    p_rs.add_argument("--role-id", dest="role_id", default=None, help="filter by role scope id")
    p_rs.add_argument("--limit", type=int, default=20)
    p_rs.set_defaults(func=cmd_role_search)

    p_bak = sub.add_parser("backup", help="snapshot the database")
    p_bak.add_argument(
        "--dest",
        default=str(DEFAULT_BACKUP_PATH),
        help=f"backup destination (default: {DEFAULT_BACKUP_PATH})",
    )
    p_bak.set_defaults(func=cmd_backup)

    p_mdb = sub.add_parser(
        "migrate-db",
        help="move legacy <repo>/.claude/memory.db to ~/.chimera/memory.db",
    )
    p_mdb.add_argument(
        "--src",
        default=str(LEGACY_DB_PATH),
        help=f"legacy database (default: {LEGACY_DB_PATH})",
    )
    p_mdb.add_argument(
        "--dest",
        default=str(Path.home() / ".chimera" / "memory.db"),
        help="new home (default: ~/.chimera/memory.db)",
    )
    p_mdb.add_argument(
        "--force",
        action="store_true",
        help="overwrite dest if it already exists",
    )
    p_mdb.set_defaults(func=cmd_migrate_db)

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
