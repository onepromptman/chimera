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
    prune          Delete rows older than N days (feedback/user exempt).
    synthesize     Collapse old rows into per-cluster digests and reclaim space.
    backup         Snapshot the database to ``<db>.bak`` next to the db file.

The store compacts toward decisions, it does not accumulate telemetry:

    decision-first ranking — a row whose ``tags`` carry the whole-word token
    ``decision`` sorts ahead of every other hit, with the relative order of
    both groups preserved (``decision_first``). It applies to the ``search``
    CLI, to ``search_general`` (the programmatic layer-1 read), and to the
    arc priors seed (``arcs/_common.priors_block``), so an operator-tagged
    decision survives the seed's 1,500-char cap ahead of run telemetry.

    synthesis — ``synthesize`` clusters aged ``arc`` rows structurally, by
    (agent, type, arc_kind, the YYYY-MM month of updated_at), and replaces
    each cluster of two or more with ``digest``-tagged rows chunked at 4,000
    chars so no member is ever dropped (see ``cmd_synthesize``). Nothing is
    parsed out of a title. ``feedback`` / ``user`` rows and anything tagged
    ``decision`` or ``digest`` are never candidates.

Default path resolution (see ``_resolve_default_db_path``):
    1. ``$CHIMERA_DB_PATH`` if set.
    2. ``~/.chimera/memory.db`` — per-machine, survives branch switches,
       gets real 0600 on Linux/macOS (WSL drvfs is a known no-op).
    3. Falls back to ``<repo>/.claude/memory.db`` ONLY if that file exists
       and the home-dir target does not — so existing checkouts keep
       working until the user runs ``migrate-db``.

All commands emit JSON on stdout; errors go to stderr with non-zero exit codes.
Read commands (``get``/``list``/``search``/``arc-search``) and ``prune``
refuse an absent database with a framed error rather than creating an empty
one (audit 2026-09-02, L6-F3/L6-F4).

``fts_or_query`` + ``search_general`` are the programmatic layer-1 read the
arc priors seed uses; everything else here is CLI surface.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
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

# Schema is verbatim from ADR-001.
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

# Layer-2/recency indexes. Created after the arc columns exist (they are added
# by _ensure_arc_columns), so they live outside SCHEMA_SQL and are applied by
# _ensure_scaling_indexes during init. Negligible at today's row counts; they
# keep arc-lookup (reflect) and freshness/recency queries index-backed as the
# store grows.
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


def _require_db(db_path: Path) -> None:
    """Read commands refuse an absent DB instead of creating an empty one.

    ``sqlite3.connect`` creates the file, so a `get`/`list`/`search` against a
    machine with no memory yet used to leave a 0-byte DB behind and then die
    with a raw "no such table: memories" traceback (audit 2026-09-02, L6-F3/
    L6-F4). Framed error, no side effect.
    """
    if not db_path.exists():
        _die(
            f"no memory DB yet at {db_path} — archive a task or run "
            "`python scripts/chimera_memory.py init` first"
        )


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
# Tag conventions — `decision` ranks, `digest` is synthesis output
# ---------------------------------------------------------------------------

# Two reserved tag tokens. `decision` is the operator's own marker for a row
# that settled something; it ranks first on every read and is never collapsed
# by synthesis. `digest` marks a row that synthesis WROTE, which is what keeps
# a second synthesize run from digesting its own digests.
DECISION_TAG = "decision"
DIGEST_TAG = "digest"

# Tags are a free-text string; the convention is comma-delimited. Split on
# commas, semicolons and whitespace so matching is whole-word: "decisions"
# and "arc-decision" are NOT the token `decision`.
_TAG_TOKEN_RE = re.compile(r"[^,;\s]+")


def tag_tokens(tags: str | None) -> list[str]:
    """Split a tag string into lowercased whole-word tokens."""
    return [t.lower() for t in _TAG_TOKEN_RE.findall(tags or "")]


def has_tag(tags: str | None, token: str) -> bool:
    """True when *tags* carries *token* as a whole word."""
    return token.lower() in tag_tokens(tags)


def decision_first(rows: list[dict]) -> list[dict]:
    """Re-rank rows so ``decision``-tagged ones come first, stable otherwise.

    A store that compacts toward decisions has to surface them first as well:
    whatever the relevance score says, a row the operator marked `decision`
    outranks a run summary. ``sorted`` is stable, so the relative order inside
    each group (bm25 for a search, recency for a listing) is untouched.
    """
    return sorted(rows, key=lambda r: 0 if has_tag(r.get("tags"), DECISION_TAG) else 1)


# A LIMIT applied before the re-rank would hide a decision that scored just
# outside the window, so every ranked read over-fetches by this factor, ranks,
# then trims back to the caller's limit.
DECISION_OVERFETCH = 5


# ---------------------------------------------------------------------------
# Programmatic read API (layer 1) — the arc priors seed-read reads through here
# ---------------------------------------------------------------------------

# The layer-1 types worth pulling forward into an arc brief: what the operator
# corrected (feedback), what a project decided (project), and distilled
# patterns. `user` (identity/preferences) and `reference` (raw lookups) are
# deliberately out: neither is a lesson about doing the work.
GENERAL_PRIOR_TYPES = ("feedback", "project", "pattern")

# Unicode word runs, not ASCII: a CJK or accented ask tokenized to nothing
# under [A-Za-z0-9], so layer 1 was unreachable for it. Quoting (below) still
# makes every token a literal.
_FTS_TOKEN_RE = re.compile(r"\w{3,}", re.UNICODE)


def fts_or_query(text: str, *, max_tokens: int = 8) -> str:
    """Build a safe FTS5 OR-query from free text.

    Every token is double-quoted, so an ask containing a reserved word (NOT,
    AND, OR, NEAR) or punctuation is matched as a literal instead of raising
    ``sqlite3.OperationalError: fts5: syntax error`` (audit 2026-09-02, P1-7).
    Returns "" when the text carries no usable token — callers treat that as
    "no query", not as an empty match.
    """
    tokens = _FTS_TOKEN_RE.findall(text or "")[:max_tokens]
    return " OR ".join(f'"{t}"' for t in tokens)


def search_general(
    *,
    query: str,
    types: tuple[str, ...] = GENERAL_PRIOR_TYPES,
    limit: int = 3,
    exclude_agent: str = "arc",
    db_path: Path | None = None,
) -> list[dict]:
    """FTS search over the LAYER-1 rows (agent != 'arc') for a priors block.

    *query* must already be an FTS5 expression (see ``fts_or_query``). Returns
    [] — never raises for an absent DB, and never creates one, so a read is
    free of side effects on a fresh machine.

    Results are ``decision_first``: a row tagged `decision` outranks the bm25
    order, so the operator's settled calls reach the priors block first.
    """
    target = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not query or not query.strip():
        return []
    if not target.exists():
        return []
    fetch = limit * DECISION_OVERFETCH
    placeholders = ", ".join("?" for _ in types)
    sql = f"""
        SELECT m.id, m.agent, m.type, m.title, m.body, m.tags, m.updated_at,
               bm25(memories_fts) AS score
        FROM memories_fts
        JOIN memories m ON m.id = memories_fts.rowid
        WHERE memories_fts MATCH ?
          AND m.agent != ?
          AND m.type IN ({placeholders})
        ORDER BY score
        LIMIT ?
    """
    params: list[object] = [query, exclude_agent, *types, fetch]
    with _connect(target) as conn:
        rows = conn.execute(sql, params).fetchall()
    return decision_first([_row_to_dict(r) for r in rows])[:limit]


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
        conn.executescript(SCALING_INDEX_SQL)  # needs the arc columns to exist first
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
    _require_db(db_path)
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

    Hits are ``decision_first``: rows tagged `decision` lead, bm25 order holds
    inside each group. The query over-fetches before the re-rank so a decision
    just outside the bm25 window is not lost to ``--limit``.
    """
    db_path = Path(args.db)
    _require_db(db_path)
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
        WHERE {" AND ".join(clauses)}
        ORDER BY score
        LIMIT ?
    """
    params.append(args.limit * DECISION_OVERFETCH)

    with _connect(db_path) as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            _die(f"FTS query failed: {exc}")

    ranked = decision_first([_row_to_dict(r) for r in rows])[: args.limit]
    _emit({"ok": True, "query": args.query, "count": len(ranked), "rows": ranked})


# ---------------------------------------------------------------------------
# Subcommand: list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> None:
    """Compact index view: id, agent, type, title, updated_at."""
    db_path = Path(args.db)
    _require_db(db_path)
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
        conn.executescript(SCALING_INDEX_SQL)  # needs the arc columns to exist first
        conn.commit()

    threshold = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
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
# Subcommand: prune
# ---------------------------------------------------------------------------

# What a prune must never sweep by default: the operator's own corrections
# (feedback) and identity/preferences (user) do not go stale the way a run
# summary does. --type names one type explicitly and overrides this.
PROTECTED_TYPES = ("feedback", "user")


def cmd_prune(args: argparse.Namespace) -> None:
    """Delete memories older than N days by updated_at.

    Retention policy, not a garbage collector: without --type, feedback and
    user rows are exempt no matter how old (see PROTECTED_TYPES). --type T
    prunes exactly T, including a protected one when the operator names it.
    --dry-run reports the same counts and deletes nothing.

    The FTS index stays in sync two ways: the memories_ad DELETE trigger is
    ensured to exist before deleting (a DB written before FTS_SYNC_SQL landed
    would otherwise keep matching pruned rows), and the external-content index
    is rebuilt afterwards, which also drops entries for rows that were never
    indexed in the first place.
    """
    db_path = Path(args.db)
    _require_db(db_path)
    if args.older_than_days < 1:
        _die("--older-than-days must be >= 1")
    if args.type is not None and args.type not in VALID_TYPES:
        _die(f"invalid --type {args.type!r}; must be one of {sorted(VALID_TYPES)}")

    # Age is measured on SQLite's own clock — the same datetime('now') that
    # stamped updated_at — so a prune cannot drift against the writes.
    offset = f"-{args.older_than_days} days"

    clauses = ["updated_at < datetime('now', ?)"]
    params: list[object] = [offset]
    if args.type is not None:
        clauses.append("type = ?")
        params.append(args.type)
        protected: tuple[str, ...] = ()
    else:
        protected = PROTECTED_TYPES
        placeholders = ", ".join("?" for _ in protected)
        clauses.append(f"(type IS NULL OR type NOT IN ({placeholders}))")
        params.extend(protected)
    where = " AND ".join(clauses)

    with _connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(FTS_SYNC_SQL)  # the delete trigger must exist first
        rows = conn.execute(
            f"SELECT type, COUNT(*) AS n FROM memories WHERE {where} GROUP BY type",
            params,
        ).fetchall()
        by_type = {(r["type"] or "untyped"): r["n"] for r in rows}
        total = sum(by_type.values())
        if not args.dry_run and total:
            conn.execute(f"DELETE FROM memories WHERE {where}", params)
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
            conn.commit()
        remaining = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
        cutoff = conn.execute("SELECT datetime('now', ?) AS t", (offset,)).fetchone()["t"]

    _emit(
        {
            "ok": True,
            "action": "prune",
            "db": str(db_path),
            "dry_run": bool(args.dry_run),
            "older_than_days": args.older_than_days,
            "cutoff": cutoff,
            "type": args.type,
            "protected_types": list(protected),
            "deleted": 0 if args.dry_run else total,
            "matched": total,
            "by_type": by_type,
            "remaining": remaining,
        }
    )


# ---------------------------------------------------------------------------
# Subcommand: synthesize
# ---------------------------------------------------------------------------

# A memory store should compact toward decisions, not accumulate telemetry.
# Synthesis is the compaction half of that: aged rows collapse into digests,
# the originals go, and VACUUM hands the pages back.
#
# The clustering rule is STRUCTURAL and reads nothing out of the title:
# (agent, type, arc_kind, the YYYY-MM month of updated_at). Title parsing was
# tried and refuted — stripping trailing digits merged rows whose trailing
# number IS their identity (ADR-006 with ADR-009, incident-1234 with
# incident-5678) and swept a year of run summaries into one cluster. A month
# bucket can do neither.

DIGEST_BODY_MAX_CHARS = 4000
# Room every part reserves for its own two header lines. The stamps are
# SQLite datetimes, so the real header is ~90 chars; the reserve is the
# guarantee that header + member lines can never overrun the cap.
DIGEST_HEADER_RESERVE = 200
# Per member: the first body line, then the whole rendered line.
DIGEST_MEMBER_BODY_CHARS = 200
DIGEST_MEMBER_LINE_CHARS = 400
# A cluster of one is not a summary of anything — a digest of a single row is
# strictly bigger than the row. Singletons are skipped and reported.
MIN_CLUSTER_SIZE = 2
# Only framework-written rows collapse by default; --agent opts others in.
DEFAULT_SYNTH_AGENTS = ("arc",)

# The read phase must not write, so a legacy store cannot be migrated on the
# way in: the arc columns are read as NULL when they are absent.
_MEMORY_BASE_COLUMNS = (
    "id",
    "agent",
    "type",
    "title",
    "body",
    "tags",
    "source_file",
    "created_at",
    "updated_at",
)
_MEMORY_ARC_COLUMNS = ("arc_kind", "arc_id")


def row_period(row: dict) -> str:
    """The ``YYYY-MM`` bucket a row's ``updated_at`` falls in."""
    stamp = str(row.get("updated_at") or "")
    return stamp[:7] if len(stamp) >= 7 else "unknown"


def cluster_key(row: dict) -> str:
    """Structural cluster key: ``agent|type|arc_kind|YYYY-MM``.

    Four facts the row already carries — nothing is parsed out of the title,
    so a title whose trailing number is its identity is never merged with a
    different one, and a bucket can never grow past one month of one arc kind.
    An absent type reads as ``untyped`` and an absent arc_kind as ``-``.
    """
    return "|".join(
        [
            str(row.get("agent") or ""),
            str(row.get("type") or "untyped"),
            str(row.get("arc_kind") or "-"),
            row_period(row),
        ]
    )


def _readable_columns(conn: sqlite3.Connection, db_path: Path) -> str:
    """Explicit SELECT list that also works on a pre-arc-columns database."""
    present = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
    if not present:
        _die(f"no memories table in {db_path} — run `python scripts/chimera_memory.py init` first")
    cols = [c for c in _MEMORY_BASE_COLUMNS if c in present]
    cols += [c if c in present else f"NULL AS {c}" for c in _MEMORY_ARC_COLUMNS]
    return ", ".join(cols)


def _first_body_line(body: str | None) -> str:
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _digest_member_line(member: dict) -> str:
    """``- <title> [<arc_id>]: <first body line>`` — one member, one line."""
    line = f"- {member.get('title')}"
    arc_id = member.get("arc_id")
    if arc_id:
        line += f" [{arc_id}]"
    first = _first_body_line(str(member.get("body") or ""))
    if first:
        line += f": {first[:DIGEST_MEMBER_BODY_CHARS]}"
    return line[:DIGEST_MEMBER_LINE_CHARS]


def _digest_body(members: list[dict], *, today: str) -> str:
    """Header (count + date range) then one line per member. Nothing dropped."""
    stamps = sorted(str(m.get("updated_at") or "")[:40] for m in members)
    header = (
        f"{len(members)} memories collapsed on {today}.\nrange: {stamps[0]} .. {stamps[-1]}\n\n"
    )
    return header + "".join(f"{_digest_member_line(m)}\n" for m in members)


def _digest_chunks(members: list[dict]) -> list[list[dict]]:
    """Split a cluster into parts that each fit DIGEST_BODY_MAX_CHARS.

    Every member lands in exactly one part, in order; a part is closed when
    the next member would not fit. There is no "…and N more" tail — a digest
    that silently drops members is not a summary of the cluster.
    """
    budget = DIGEST_BODY_MAX_CHARS - DIGEST_HEADER_RESERVE
    parts: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for member in members:
        cost = len(_digest_member_line(member)) + 1
        if current and used + cost > budget:
            parts.append(current)
            current, used = [], 0
        current.append(member)
        used += cost
    if current:
        parts.append(current)
    return parts


def _digest_tags(members: list[dict]) -> str:
    """Union of every member's tags, plus the reserved ``digest`` token."""
    tokens = {DIGEST_TAG}
    for member in members:
        tokens.update(tag_tokens(member.get("tags")))
    return ",".join(sorted(tokens))


def default_export_path(today: str) -> Path:
    """``wip/memory-digest-<YYYY-MM-DD>.md`` under the repo root.

    ``wip/`` is a gitignored zone, so an export can never become a tracked
    file (see .gitignore and tests/test_memory_synthesize.py).
    """
    return PROJECT_ROOT / "wip" / f"memory-digest-{today}.md"


def _md_row_line(row: dict) -> str:
    first = _first_body_line(str(row.get("body") or ""))
    stamp = str(row.get("updated_at") or "")
    who = f"{row.get('agent')}/{row.get('type') or 'untyped'}"
    line = f"- **{row.get('title')}** — {who} · {stamp}"
    return f"{line} — {first}" if first else line


def _by_recency(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (str(r.get("updated_at") or ""), str(r.get("title") or "")),
        reverse=True,
    )


def export_markdown(rows: list[dict], *, db_path: Path, today: str, planned: bool) -> str:
    """Render the whole store as markdown: decisions, digests, then the rest.

    ``planned`` marks a --dry-run export, which describes the store as it
    WOULD be after the synthesis rather than as it is.
    """
    decisions = [r for r in rows if has_tag(r.get("tags"), DECISION_TAG)]
    digests = [
        r
        for r in rows
        if has_tag(r.get("tags"), DIGEST_TAG) and not has_tag(r.get("tags"), DECISION_TAG)
    ]
    ranked_ids = {id(r) for r in decisions} | {id(r) for r in digests}
    rest = [r for r in rows if id(r) not in ranked_ids]

    state = "planned state (dry run)" if planned else "current state"
    out = [
        f"# Memory digest — {today}",
        "",
        f"Store: `{db_path}` · {len(rows)} rows · {state}.",
        "",
        f"## Decisions ({len(decisions)})",
        "",
    ]
    out += [_md_row_line(r) for r in _by_recency(decisions)] or ["_none_"]
    out += ["", f"## Digests ({len(digests)})", ""]
    out += [_md_row_line(r) for r in _by_recency(digests)] or ["_none_"]
    out += ["", "## Everything else", ""]
    if not rest:
        out += ["_none_", ""]
    for mem_type in sorted({str(r.get("type") or "untyped") for r in rest}):
        grouped = [r for r in rest if str(r.get("type") or "untyped") == mem_type]
        out += [f"### {mem_type} ({len(grouped)})", ""]
        out += [_md_row_line(r) for r in _by_recency(grouped)]
        out += [""]
    return "\n".join(out).rstrip() + "\n"


def _snapshot_target(db_path: Path, stamp: str) -> Path:
    """``<db>.<YYYYMMDD-HHMMSS>.bak``, serialized so it never overwrites one."""
    candidate = db_path.with_name(f"{db_path.name}.{stamp}.bak")
    serial = 2
    while candidate.exists():
        candidate = db_path.with_name(f"{db_path.name}.{stamp}-{serial}.bak")
        serial += 1
    return candidate


def cmd_synthesize(args: argparse.Namespace) -> None:
    """Collapse aged rows into per-month digests, then reclaim the space.

    THE RULE. Candidates are rows older than N days written by agent ``arc``
    (``--agent NAME``, repeatable, opts another agent's rows in), minus three
    absolute exemptions: ``feedback``/``user`` types (PROTECTED_TYPES, as in
    ``prune``; ``--type`` narrows candidates and can never widen them), rows
    tagged ``decision`` (what the store compacts toward), and rows tagged
    ``digest`` (synthesis' own output — the dedup rule that makes a second run
    a no-op). Candidates group by ``cluster_key`` — agent, type, arc_kind and
    the YYYY-MM month of updated_at, no title parsing — and a group of fewer
    than MIN_CLUSTER_SIZE members is left untouched and reported as a skipped
    singleton. Each remaining cluster becomes one digest row per PART: parts
    chunk the member lines at DIGEST_BODY_MAX_CHARS so every member appears in
    exactly one part and none is ever dropped. A digest keeps its members'
    agent/type/arc_kind (no new type, no migration), carries the union of
    their tags plus ``digest``, has a NULL arc_id, and is titled
    ``digest:<agent>|<type>|<arc_kind>|<YYYY-MM>:part-<k>``.

    Write path: snapshot to ``<db>.<YYYYMMDD-HHMMSS>.bak`` (unless
    ``--no-backup``), then ONE transaction that inserts the digests and
    deletes the members — the FTS index follows through the existing triggers
    and a final rebuild — then ``VACUUM`` outside that transaction. Any
    sqlite3 error inside rolls the transaction back and exits 1 with the
    snapshot named. ``--dry-run`` performs ZERO writes (not even a schema
    ensure) and prints the plan; ``--export PATH`` writes a markdown digest of
    the resulting — or, under --dry-run, the planned — store.
    """
    db_path = Path(args.db)
    _require_db(db_path)
    if args.older_than_days < 1:
        _die("--older-than-days must be >= 1")
    if args.type is not None:
        if args.type not in VALID_TYPES:
            _die(f"invalid --type {args.type!r}; must be one of {sorted(VALID_TYPES)}")
        if args.type in PROTECTED_TYPES:
            _die(
                f"--type {args.type!r} is never synthesized; {'/'.join(PROTECTED_TYPES)} are exempt"
            )
    agents = sorted({*DEFAULT_SYNTH_AGENTS, *(args.agent or [])})

    offset = f"-{args.older_than_days} days"
    protected_marks = ", ".join("?" for _ in PROTECTED_TYPES)
    agent_marks = ", ".join("?" for _ in agents)
    clauses = [
        "updated_at < datetime('now', ?)",
        f"(type IS NULL OR type NOT IN ({protected_marks}))",
        f"agent IN ({agent_marks})",
    ]
    params: list[object] = [offset, *PROTECTED_TYPES, *agents]
    if args.type is not None:
        clauses.append("type = ?")
        params.append(args.type)
    where = " AND ".join(clauses)

    bytes_before = db_path.stat().st_size

    # --- read phase: no writes, so --dry-run cannot touch the file --------
    conn = _connect(db_path)
    try:
        columns = _readable_columns(conn, db_path)
        today = str(conn.execute("SELECT date('now') AS d").fetchone()["d"])
        now = str(conn.execute("SELECT datetime('now') AS t").fetchone()["t"])
        cutoff = str(conn.execute("SELECT datetime('now', ?) AS t", (offset,)).fetchone()["t"])
        candidates = [
            _row_to_dict(r)
            for r in conn.execute(
                f"SELECT {columns} FROM memories WHERE {where} ORDER BY id", params
            )
        ]
        all_rows = [
            _row_to_dict(r) for r in conn.execute(f"SELECT {columns} FROM memories ORDER BY id")
        ]
    finally:
        conn.close()

    grouped: dict[str, list[dict]] = {}
    for row in candidates:
        tags = row.get("tags")
        if has_tag(tags, DECISION_TAG) or has_tag(tags, DIGEST_TAG):
            continue
        grouped.setdefault(cluster_key(row), []).append(row)

    keys = sorted(grouped)
    clusters = [k for k in keys if len(grouped[k]) >= MIN_CLUSTER_SIZE]
    singletons = [m for k in keys if len(grouped[k]) < MIN_CLUSTER_SIZE for m in grouped[k]]

    digests: list[dict] = []
    plan: list[dict] = []
    for key in clusters:
        members = grouped[key]
        head = members[0]
        chunks = _digest_chunks(members)
        for part, chunk in enumerate(chunks, start=1):
            digests.append(
                {
                    "agent": head.get("agent"),
                    "type": head.get("type"),
                    "arc_kind": head.get("arc_kind"),
                    "arc_id": None,
                    "title": f"digest:{key}:part-{part}",
                    "body": _digest_body(chunk, today=today),
                    "tags": _digest_tags(chunk),
                    "updated_at": now,
                }
            )
        plan.append(
            {
                "key": key,
                "period": row_period(head),
                "members": len(members),
                "parts": len(chunks),
                "member_titles": [str(m.get("title")) for m in members],
            }
        )

    doomed = [m["id"] for key in clusters for m in grouped[key]]

    # --- write phase ------------------------------------------------------
    backup_path: str | None = None
    bytes_after: int | None = None
    if not args.dry_run and digests:
        if not args.no_backup:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            backup_path = str(snapshot_db(db_path, _snapshot_target(db_path, stamp)))
        snapshot_note = backup_path or "none taken (--no-backup)"
        conn = _connect(db_path)
        try:
            conn.executescript(SCHEMA_SQL)
            conn.executescript(FTS_SYNC_SQL)  # the delete trigger must exist first
            _ensure_arc_columns(conn)
            conn.commit()
            conn.isolation_level = None  # explicit transaction; VACUUM needs autocommit
            conn.execute("BEGIN IMMEDIATE")
            try:
                for digest_row in digests:
                    conn.execute(
                        """INSERT INTO memories
                           (agent, type, title, body, tags, source_file,
                            arc_kind, arc_id, updated_at)
                           VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, ?)""",
                        (
                            digest_row["agent"],
                            digest_row["type"],
                            digest_row["title"],
                            digest_row["body"],
                            digest_row["tags"],
                            digest_row["arc_kind"],
                            digest_row["updated_at"],
                        ),
                    )
                conn.executemany(
                    "DELETE FROM memories WHERE id = ?", [(row_id,) for row_id in doomed]
                )
                conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                conn.execute("ROLLBACK")
                _die(
                    f"synthesize failed and rolled back; snapshot intact at {snapshot_note}: {exc}"
                )
            conn.execute("VACUUM")  # outside the transaction, by contract
        finally:
            conn.close()
    if not args.dry_run:
        bytes_after = db_path.stat().st_size

    # --- export -----------------------------------------------------------
    export_path: str | None = None
    if args.export is not None:
        target = Path(args.export) if args.export else default_export_path(today)
        if args.dry_run:
            gone = set(doomed)
            final_rows = [r for r in all_rows if r["id"] not in gone] + digests
        else:
            conn = _connect(db_path)
            try:
                cols = _readable_columns(conn, db_path)
                final_rows = [
                    _row_to_dict(r)
                    for r in conn.execute(f"SELECT {cols} FROM memories ORDER BY id")
                ]
            finally:
                conn.close()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            export_markdown(final_rows, db_path=db_path, today=today, planned=bool(args.dry_run)),
            encoding="utf-8",
        )
        export_path = str(target)

    _emit(
        {
            "ok": True,
            "action": "synthesize",
            "db": str(db_path),
            "dry_run": bool(args.dry_run),
            "older_than_days": args.older_than_days,
            "agents": agents,
            "type": args.type,
            "cutoff": cutoff,
            "protected_types": list(PROTECTED_TYPES),
            "clusters": plan,
            "skipped_singletons": {
                "count": len(singletons),
                "titles": [str(m.get("title")) for m in singletons],
            },
            "rows_deleted": len(doomed),
            "rows_inserted": len(digests),
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
            "backup_path": backup_path,
            "export_path": export_path,
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

    _require_db(Path(args.db))

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


def snapshot_db(src_path: Path, dst_path: Path) -> Path:
    """Atomic snapshot via the SQLite online backup API. Returns *dst_path*.

    The one backup implementation in this module: ``backup`` is its CLI face
    and ``synthesize`` calls it before it collapses anything.
    """
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(src_path)) as src, sqlite3.connect(str(dst_path)) as dst:
        src.backup(dst)
    return dst_path


def cmd_backup(args: argparse.Namespace) -> None:
    """Atomic SQLite backup using the online backup API."""
    src_path = Path(args.db)
    dst_path = Path(args.dest)
    if not src_path.exists():
        _die(f"source database does not exist: {src_path}")
    snapshot_db(src_path, dst_path)

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

    sub.add_parser("init", help="create database + schema + FTS5 table").set_defaults(func=cmd_init)

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
    p_aw.add_argument(
        "--kind", required=True, help="arc kind (e.g. research, design, or any string)"
    )
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

    p_prune = sub.add_parser(
        "prune", help="delete memories older than N days (feedback/user exempt)"
    )
    p_prune.add_argument(
        "--older-than-days",
        dest="older_than_days",
        type=int,
        required=True,
        help="delete rows whose updated_at is older than this many days",
    )
    p_prune.add_argument(
        "--type",
        default=None,
        help=(f"prune exactly this type (overrides the {'/'.join(PROTECTED_TYPES)} exemption)"),
    )
    p_prune.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report what would be deleted and delete nothing",
    )
    p_prune.set_defaults(func=cmd_prune)

    p_syn = sub.add_parser(
        "synthesize",
        help="collapse rows older than N days into per-month digests, then VACUUM",
    )
    p_syn.add_argument(
        "--older-than-days",
        dest="older_than_days",
        type=int,
        required=True,
        help="cluster rows whose updated_at is older than this many days",
    )
    p_syn.add_argument(
        "--agent",
        action="append",
        default=None,
        help=(
            "also collapse this agent's rows (repeatable); by default only "
            f"{'/'.join(DEFAULT_SYNTH_AGENTS)} rows are candidates"
        ),
    )
    p_syn.add_argument(
        "--type",
        default=None,
        help=(
            "narrow candidates to exactly this type "
            f"({'/'.join(PROTECTED_TYPES)} are never synthesized)"
        ),
    )
    p_syn.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print the plan as JSON and write nothing to the database",
    )
    p_syn.add_argument(
        "--export",
        nargs="?",
        const="",
        default=None,
        help="write a markdown digest of the store (no value: wip/memory-digest-<date>.md)",
    )
    p_syn.add_argument(
        "--no-backup",
        dest="no_backup",
        action="store_true",
        help="skip the pre-write snapshot to <db>.bak",
    )
    p_syn.set_defaults(func=cmd_synthesize)

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
